#!/usr/bin/env python3
"""memory-snapshot — keep each version of the agent's auto-memory notes.

Claude Code keeps auto-memory notes in ~/.claude/projects/<project>/memory/:
MEMORY.md, the index, plus one file per note. It overwrites them in place and
keeps no history, so nobody can later see when a judgment on one piece became
a standing rule. At session end this script compares every note with the last
state it saw for that project, and stores each changed note, redacted, in the
session's material folder:

    <material>/memory/<sha12>-<name>     one note version (redacted text)
    <material>/memory/index.json         {project, manifest: {name: sha},
                                          changed, deleted, too_large, at}

The sha is of the stored (redacted) text, so a file's name is the hash of
its own bytes. The state of each project lives in
.system/memory-state/<hash of the project folder>.json.

  * First run for a project stores every note.
  * An unchanged folder writes nothing and creates no folder.
  * Fail closed: with no redactor, no text is written and the state does not
    move, so the next run with a redactor stores the change.
  * A later change found at another session's end belongs to that session:
    the state is per project, not per session.
  * Nested folders under memory/ are walked; a nested name keeps its path
    with "/" written as "__".

Like the rest of sessions/material, these are originals, never memory:
nothing indexes them.

Usage:
    memory-snapshot.py <transcript.jsonl> <material_dir> [--state-dir DIR]
    memory-snapshot.py --selftest
"""

import fcntl
import hashlib
import importlib.util
import json
import os
import sys
import time

MAX_BYTES = 256 * 1024


def load_redactor(here):
    p = os.path.join(here, "redact-transcript.py")
    try:
        spec = importlib.util.spec_from_file_location("redact_transcript", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.redact("probe")
        return mod.redact
    except Exception:
        return None


def notes(memdir):
    """{relative name: absolute path} for every regular, non-hidden file."""
    out = {}
    for root, dirs, files in os.walk(memdir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            if f.startswith("."):
                continue
            p = os.path.join(root, f)
            if os.path.isfile(p) and not os.path.islink(p):
                out[os.path.relpath(p, memdir)] = p
    return out


def _write(path, data):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def snapshot(transcript, material, state_dir, redact="load"):
    """Returns a one-line summary. Raises only on programming errors."""
    if redact == "load":
        redact = load_redactor(os.path.dirname(os.path.abspath(__file__)))
    project = os.path.dirname(os.path.abspath(transcript))
    memdir = os.path.join(project, "memory")
    if not os.path.isdir(memdir):
        return "no memory folder"
    if redact is None:
        return "SKIPPED, redactor missing (failing closed)"

    os.makedirs(state_dir, exist_ok=True)
    key = hashlib.sha256(project.encode()).hexdigest()[:16]
    state_path = os.path.join(state_dir, key + ".json")
    with open(state_path + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            prev = json.load(open(state_path)).get("notes", {})
        except Exception:
            prev = {}

        manifest, changed, too_large, blobs = {}, [], [], {}
        for name, path in notes(memdir).items():
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    too_large.append(name)
                    continue
                raw = open(path, "rb").read()
            except OSError:
                continue
            text, _ = redact(raw.decode("utf-8", errors="replace"))
            data = text.encode("utf-8")
            sha = hashlib.sha256(data).hexdigest()
            manifest[name] = sha
            if prev.get(name) != sha:
                changed.append(name)
                blobs[name] = (sha, data)
        # A note that grew past the limit is not deleted, only not stored.
        deleted = sorted(n for n in prev if n not in manifest and n not in too_large)
        if not changed and not deleted:
            return "unchanged"

        out = os.path.join(material, "memory")
        os.makedirs(out, exist_ok=True)
        for name, (sha, data) in blobs.items():
            _write(os.path.join(out, "%s-%s" % (sha[:12], name.replace(os.sep, "__"))), data)

        # A resumed session runs this again into the same folder: keep what
        # the earlier run recorded.
        ip = os.path.join(out, "index.json")
        try:
            old = json.load(open(ip))
        except Exception:
            old = {}
        idx = {
            "project": os.path.basename(project),
            "manifest": manifest,
            "changed": sorted(set(old.get("changed", [])) | set(changed)),
            "deleted": sorted((set(old.get("deleted", [])) - set(manifest)) | set(deleted)),
            "too_large": sorted(too_large),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        _write(ip, (json.dumps(idx, indent=1, sort_keys=True) + "\n").encode())
        # State moves last: a crash before this line stores the change again.
        keep = {n: s for n, s in prev.items() if n in too_large}
        keep.update(manifest)
        _write(state_path, (json.dumps({"project": project, "notes": keep}, indent=1, sort_keys=True) + "\n").encode())
    return "%d changed, %d deleted%s" % (
        len(changed), len(deleted), ", %d too large" % len(too_large) if too_large else "")


def selftest():
    import tempfile

    failures = []

    def check(name, ok, detail=""):
        print(("ok " if ok else "FAIL ") + name + (": " + detail if detail and not ok else ""))
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as td:
        proj = os.path.join(td, "projects", "-synthetic-project")
        mem = os.path.join(proj, "memory")
        os.makedirs(os.path.join(mem, "sub"))
        t = os.path.join(proj, "0000aaaa-0000.jsonl")
        open(t, "w").write("{}\n")
        open(os.path.join(mem, "MEMORY.md"), "w").write("# Memory Index\n- [A](a-note.md)\n")
        open(os.path.join(mem, "a-note.md"), "w").write("Say less.\nAPI_KEY = 'Zq81Zq81Zq81Zq81'\n")
        open(os.path.join(mem, "sub", "nested.md"), "w").write("nested note\n")
        open(os.path.join(mem, ".DS_Store"), "w").write("x")
        state = os.path.join(td, "state")
        m1 = os.path.join(td, "material", "s1")

        r = snapshot(t, m1, state)
        idx = json.load(open(os.path.join(m1, "memory", "index.json")))
        files = sorted(os.listdir(os.path.join(m1, "memory")))
        check("first run: every note stored", sorted(idx["changed"]) == ["MEMORY.md", "a-note.md", "sub/nested.md"]
              and len(files) == 4, "%s %s" % (r, files))
        check("first run: hidden files skipped", not any("DS_Store" in f for f in files))
        check("first run: nested name flattened", any(f.endswith("-sub__nested.md") for f in files))
        body = open(os.path.join(m1, "memory", [f for f in files if f.endswith("-a-note.md")][0])).read()
        check("stored text is redacted", "Zq81Zq81" not in body and "Say less." in body)
        sha = idx["manifest"]["a-note.md"]
        check("sha is of the stored bytes", hashlib.sha256(body.encode()).hexdigest() == sha
              and any(f.startswith(sha[:12]) for f in files))
        check("index fields", set(idx) == {"project", "manifest", "changed", "deleted", "too_large", "at"}
              and idx["project"] == "-synthetic-project")

        m2 = os.path.join(td, "material", "s2")
        r = snapshot(t, m2, state)
        check("second run: nothing written, no folder", r == "unchanged" and not os.path.exists(m2), r)

        open(os.path.join(mem, "a-note.md"), "w").write("Say less, except in docs.\n")
        os.remove(os.path.join(mem, "sub", "nested.md"))
        m3 = os.path.join(td, "material", "s3")
        r = snapshot(t, m3, state)
        idx3 = json.load(open(os.path.join(m3, "memory", "index.json")))
        files3 = [f for f in os.listdir(os.path.join(m3, "memory")) if f != "index.json"]
        check("changed note: only it is stored", idx3["changed"] == ["a-note.md"] and len(files3) == 1, "%s %s" % (r, files3))
        check("deleted note: listed", idx3["deleted"] == ["sub/nested.md"], str(idx3["deleted"]))
        check("manifest is the whole folder", sorted(idx3["manifest"]) == ["MEMORY.md", "a-note.md"])

        open(os.path.join(mem, "big.md"), "w").write("x" * (MAX_BYTES + 1))
        m4 = os.path.join(td, "material", "s4")
        r = snapshot(t, m4, state)
        check("too large: not stored, not a change",
              r == "unchanged" and not os.path.exists(m4), r)
        os.remove(os.path.join(mem, "big.md"))

        open(os.path.join(mem, "b-note.md"), "w").write("new note\n")
        m5 = os.path.join(td, "material", "s5")
        r = snapshot(t, m5, state, redact=None)
        check("no redactor: no text written, no folder", "SKIPPED" in r and not os.path.exists(m5), r)
        r = snapshot(t, m5, state)
        check("no redactor: the change is stored on the next run",
              json.load(open(os.path.join(m5, "memory", "index.json")))["changed"] == ["b-note.md"], r)

        # Same session again (a resume): earlier changes stay in its index.
        open(os.path.join(mem, "MEMORY.md"), "a").write("- [B](b-note.md)\n")
        snapshot(t, m5, state)
        idx5 = json.load(open(os.path.join(m5, "memory", "index.json")))
        check("resume: index keeps earlier changes", idx5["changed"] == ["MEMORY.md", "b-note.md"], str(idx5["changed"]))

        t2 = os.path.join(td, "projects", "-no-memory", "x.jsonl")
        os.makedirs(os.path.dirname(t2))
        open(t2, "w").write("{}\n")
        check("no memory folder: nothing", snapshot(t2, os.path.join(td, "m6"), state) == "no memory folder"
              and not os.path.exists(os.path.join(td, "m6")))

    if failures:
        print("%d FAILED" % len(failures))
        sys.exit(1)
    print("memory-snapshot selftest: ALL PASS")


def main():
    a = sys.argv[1:]
    if a[:1] == ["--selftest"]:
        selftest()
        return
    state_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory-state")
    if "--state-dir" in a:
        i = a.index("--state-dir")
        state_dir = a[i + 1]
        a = a[:i] + a[i + 2:]
    if len(a) != 2:
        print("usage: memory-snapshot.py <transcript.jsonl> <material_dir> [--state-dir DIR]", file=sys.stderr)
        sys.exit(2)
    print(snapshot(a[0], a[1], state_dir))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        # The hook logs this; it must never stop the session's other stages.
        print("FAILED: %s" % e)
        sys.exit(1)
