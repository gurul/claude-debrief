#!/usr/bin/env python3
"""capture-status — one small record of which capture stage is failing.

The SessionEnd hook runs several stages per session (archive, material,
draft, memory, reactions, and whatever other hooks add). Before this file a
failed stage only wrote a line to hook.log, among thousands of routine lines,
so a stage could fail for weeks unseen. Each stage now marks itself here, and
the SessionStart hook prints one clause while any stage is failing.

State: .system/capture_status.json (machine state, gitignored), one object
per stage:

    {"material": {"last_ok_at": "...", "failed_since_ok": 2,
                  "first_failed_at": "...", "last_error": "..."}}

Writes go through a temp file and os.replace, under an exclusive flock on a
sibling lock file, so concurrent session ends never lose a mark or leave a
half-written file.

Usage:
    capture-status.py [--dir SYSTEM_DIR] mark <stage> ok|fail [message...]
    capture-status.py [--dir SYSTEM_DIR] report
    capture-status.py --selftest

SYSTEM_DIR defaults to this script's own folder (not resolved through a
symlink, the same as the shell hooks). It never reads DEBRIEF_DIR from the
environment: a test copy of the scripts must not write into the live store.
"""

import fcntl
import json
import os
import re
import sys
import time

STATE = "capture_status.json"
MAX_ERROR = 240


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _load(path):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        # Missing or damaged: start over. This is a status line, not a ledger.
        return {}


def _clean(message):
    """One line, short, and through the redactor when it is there."""
    msg = re.sub(r"\s+", " ", message or "").strip()
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "redact-transcript.py")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("redact_transcript", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        msg, _ = mod.redact(msg)
    except Exception:
        # No redactor: keep only the first words, which are ours, not the
        # session's.
        msg = " ".join(msg.split()[:6])
    return msg[:MAX_ERROR]


def mark(system_dir, stage, result, message=""):
    if result not in ("ok", "fail"):
        raise ValueError("result must be ok or fail")
    stage = re.sub(r"[^A-Za-z0-9_.-]", "_", stage)[:40] or "unknown"
    path = os.path.join(system_dir, STATE)
    os.makedirs(system_dir, exist_ok=True)
    with open(path + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = _load(path)
        s = d.get(stage) if isinstance(d.get(stage), dict) else {}
        s.setdefault("last_ok_at", None)
        s.setdefault("failed_since_ok", 0)
        s.setdefault("first_failed_at", None)
        s.setdefault("last_error", None)
        t = now()
        if result == "ok":
            s["last_ok_at"] = t
            s["failed_since_ok"] = 0
            s["first_failed_at"] = None
        else:
            if not s["failed_since_ok"]:
                s["first_failed_at"] = t
            s["failed_since_ok"] = int(s["failed_since_ok"] or 0) + 1
            s["last_error"] = _clean(message) or "no message"
        d[stage] = s
        tmp = "%s.tmp.%d" % (path, os.getpid())
        with open(tmp, "w") as f:
            json.dump(d, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)


def report(system_dir):
    """One clause while a stage is failing; empty string when all is well."""
    d = _load(os.path.join(system_dir, STATE))
    bad = []
    for stage in sorted(d):
        s = d[stage]
        if not isinstance(s, dict) or not s.get("failed_since_ok"):
            continue
        n = int(s["failed_since_ok"])
        since = (s.get("first_failed_at") or "")[:10]
        err = (s.get("last_error") or "")[:90]
        bad.append("%s (%d failed run%s since %s%s)" % (
            stage, n, "" if n == 1 else "s", since or "unknown",
            ": " + err if err else ""))
    if not bad:
        return ""
    return ("Debrief capture is failing: " + "; ".join(bad)
            + ". Details are in .system/hook.log.")


def selftest():
    import subprocess
    import tempfile

    failures = []

    def check(name, ok, detail=""):
        print(("ok " if ok else "FAIL ") + name + (": " + detail if detail and not ok else ""))
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as td:
        check("silent when nothing has run", report(td) == "")
        check("no state file before any mark", not os.path.exists(os.path.join(td, STATE)))

        mark(td, "archive", "ok")
        check("silent when every stage is fine", report(td) == "")

        mark(td, "material", "fail", "extract-material.py missing\nsecond line")
        mark(td, "material", "fail", "extract-material.py missing")
        d = _load(os.path.join(td, STATE))
        m = d.get("material", {})
        check("fail: counter and first failure recorded",
              m.get("failed_since_ok") == 2 and m.get("first_failed_at") and m.get("last_ok_at") is None,
              json.dumps(m))
        check("fail: message kept on one line", "\n" not in (m.get("last_error") or "x\n"))
        r = report(td)
        check("report names the failing stage only",
              "material (2 failed runs" in r and "archive" not in r, r)
        check("report is one line", r.count("\n") == 0 and r.startswith("Debrief capture is failing"), r)

        mark(td, "material", "ok")
        m = _load(os.path.join(td, STATE)).get("material", {})
        check("recover: counter reset, report silent",
              m.get("failed_since_ok") == 0 and m.get("first_failed_at") is None and report(td) == "",
              json.dumps(m))

        mark(td, "a stage/with odd name", "fail", "x")
        check("free-form stage names are kept, made safe",
              "a_stage_with_odd_name" in _load(os.path.join(td, STATE)))
        mark(td, "a stage/with odd name", "ok")

        # Concurrent marks from separate processes: none lost, file stays JSON.
        me = os.path.abspath(__file__)
        procs = [subprocess.Popen([sys.executable, me, "--dir", td, "mark", "turns", "fail", "boom %d" % i])
                 for i in range(12)]
        procs += [subprocess.Popen([sys.executable, me, "--dir", td, "mark", "s%d" % i, "ok"])
                  for i in range(8)]
        rcs = [p.wait() for p in procs]
        d = _load(os.path.join(td, STATE))
        check("concurrent: every process exited 0", rcs == [0] * len(rcs), str(rcs))
        check("concurrent: no fail mark lost", d.get("turns", {}).get("failed_since_ok") == 12,
              json.dumps(d.get("turns")))
        check("concurrent: no stage lost", all("s%d" % i in d for i in range(8)))
        check("concurrent: no temp files left",
              not [x for x in os.listdir(td) if ".tmp." in x], str(os.listdir(td)))

        # Secret-shaped text in a message never lands in the state file.
        mark(td, "draft", "fail", "drafter said DB_PASSWORD=hunter2hunter2")
        check("message goes through the redactor",
              "hunter2hunter2" not in open(os.path.join(td, STATE)).read())

        # A damaged state file is replaced, not fatal.
        open(os.path.join(td, STATE), "w").write("{not json")
        mark(td, "archive", "fail", "x")
        check("damaged state file recovers", _load(os.path.join(td, STATE)).get("archive", {}).get("failed_since_ok") == 1)

        # CLI: report prints nothing on a clean store, exit 0.
        with tempfile.TemporaryDirectory() as clean:
            out = subprocess.run([sys.executable, me, "--dir", clean, "report"], capture_output=True, text=True)
            check("cli: report on a clean store prints nothing", out.returncode == 0 and out.stdout == "", repr(out.stdout))
        out = subprocess.run([sys.executable, me, "--dir", td, "mark", "x", "maybe"], capture_output=True, text=True)
        check("cli: a bad call still exits 0 and prints nothing", out.returncode == 0 and out.stdout == "")

    if failures:
        print("%d FAILED" % len(failures))
        sys.exit(1)
    print("capture-status selftest: ALL PASS")


def main():
    a = sys.argv[1:]
    if a[:1] == ["--selftest"]:
        selftest()
        return
    system_dir = os.path.dirname(os.path.abspath(__file__))
    if a[:1] == ["--dir"] and len(a) >= 2:
        system_dir, a = a[1], a[2:]
    try:
        if a[:1] == ["mark"] and len(a) >= 3:
            mark(system_dir, a[1], a[2], " ".join(a[3:]))
        elif a[:1] == ["report"]:
            r = report(system_dir)
            if r:
                print(r)
        else:
            print("usage: capture-status.py [--dir D] mark <stage> ok|fail [message] | report", file=sys.stderr)
    except Exception as e:
        # Called from hooks: never raise into a session.
        print("capture-status: %s" % e, file=sys.stderr)


if __name__ == "__main__":
    main()
