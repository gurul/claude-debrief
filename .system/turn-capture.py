#!/usr/bin/env python3
"""turn-capture — keep every version of the design files a session tried, turn by turn.

extract-material.py learns about code from the edit calls in the transcripts,
at session end. A file changed by a shell command, and every middle version that
was tried and then replaced inside the session, never reaches it. This hook
takes a snapshot of the design files at each prompt and at each Stop, and
keeps the difference:

    sessions/material/<sid8>/turns/
      <NNN>-agent.patch      what changed while the agent worked on a prompt
      <NNN>-between.patch    what changed while the agent was idle (unverified)
      index.jsonl            one line per patch: files, counts, trees, doubts

The snapshot is a git tree in a private index and a private object store under
$DEBRIEF_DIR/.system/turns/. The repo's own objects are read as an alternate.
Nothing is written to the repo: not its index, not its objects, not a ref.

A between patch is not proof that the human edited a file. Another session, a
formatter, a background task or an interrupted turn can all write in that gap,
so each between line says attribution "unverified" and lists its doubts.

Hook contract: print nothing on stdout (UserPromptSubmit stdout goes into the
model's context), always exit 0, and stay under a second on UserPromptSubmit.
The Stop work runs in a detached child so the hook returns at once.

Usage (hooks; the hook JSON arrives on stdin):
    turn-capture.py --event UserPromptSubmit|Stop|SessionEnd
    turn-capture.py cleanup --session <id>
    turn-capture.py --selftest

Off unless DEBRIEF_TURNS=1. See docs/turns.md.
"""

import datetime
import fcntl
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_PATCH = 256 * 1024
MAX_FILES = 50
MAX_PATHSPEC = 1000
UPS_BUDGET = 0.8  # seconds of git work allowed on UserPromptSubmit
CHILD_GIT_TIMEOUT = 30
PRUNE_DAYS = 14
MARKER_DAYS = 2
MAX_LOG = 256 * 1024
MAX_TRANSCRIPT_SCAN = 64 * 1024 * 1024

# Web design files: a change to one of these asks for screenshots.
WEB_EXT = {
    ".tsx", ".jsx", ".vue", ".svelte", ".astro", ".html", ".htm", ".css",
    ".scss", ".sass", ".less", ".svg", ".mdx",
}
WEB_NAMES = ["tailwind.config.*", "theme.*", "*.theme.*", "*tokens*.json", "*tokens*.ts"]
# Never snapshotted, whatever the design set says.
LOCKFILES = {
    "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock",
    "bun.lockb", "Podfile.lock", "Package.resolved", "Cargo.lock", "Gemfile.lock",
    "composer.lock", "poetry.lock", "uv.lock", "pubspec.lock", "deno.lock",
}
GENERATED_DIRS = ("node_modules/", ".next/", ".svelte-kit/", ".turbo/", ".git/")
DOUBTS = {
    "interrupted_turn": "no Stop was seen for the previous prompt; the agent's unfinished work may be in this patch",
    "head_moved": "HEAD moved in the gap",
    "other_session": "another session was active in this repo in the gap",
    "background_task": "a background task the agent started was still running at Stop",
}

_design = None
_redactor = "unloaded"


class GiveUp(Exception):
    """A git call failed or the time budget ran out. Never leaves this module."""


# ---------------------------------------------------------------- basics


def debrief_dir():
    return os.environ.get("DEBRIEF_DIR") or os.path.dirname(HERE)


def store(*parts):
    return os.path.join(debrief_dir(), ".system", "turns", *parts)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    try:
        p = store("turns.log")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p) and os.path.getsize(p) > MAX_LOG:
            os.replace(p, p + ".1")
        with open(p, "a") as f:
            f.write("%s %s\n" % (now_iso(), msg))
    except Exception:
        pass


def enabled(var):
    return os.environ.get(var, "").strip() not in ("", "0")


def is_design(path):
    """extract-material.py's design set, minus lockfiles, env files and build output."""
    global _design
    base = os.path.basename(path)
    if base in LOCKFILES or base.endswith(".lock") or base.startswith(".env"):
        return False
    p = "/" + path
    if any("/" + d in p for d in GENERATED_DIRS):
        return False
    if _design is None:
        try:
            spec = importlib.util.spec_from_file_location("extract_material", os.path.join(HERE, "extract-material.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _design = mod.is_design
        except Exception:
            _design = _design_mirror
    try:
        return bool(_design(path))
    except Exception:
        return _design_mirror(path)


def _design_mirror(path):
    # A copy of extract-material.py's set, used only when that file cannot load.
    ext = {".tsx", ".jsx", ".vue", ".svelte", ".astro", ".html", ".htm", ".css", ".scss", ".sass",
           ".less", ".svg", ".swift", ".dart", ".mdx"}
    names = ["design*.md", "DESIGN*.md", "*tokens*.json", "*tokens*.ts", "tailwind.config.*",
             "theme.*", "*.theme.*", "*.storyboard", "*.xib"]
    base = os.path.basename(path)
    return os.path.splitext(base)[1].lower() in ext or any(fnmatch.fnmatch(base, n) for n in names)


def is_web(path):
    base = os.path.basename(path)
    return os.path.splitext(base)[1].lower() in WEB_EXT or any(fnmatch.fnmatch(base, n) for n in WEB_NAMES)


def redactor():
    """The sibling redactor's redact(text) -> (text, counts), or None. Loaded once."""
    global _redactor
    if _redactor == "unloaded":
        try:
            spec = importlib.util.spec_from_file_location("redact_transcript", os.path.join(HERE, "redact-transcript.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.redact("probe")
            _redactor = mod.redact
        except Exception:
            _redactor = None
    return _redactor


def mark(status, msg, stage="turns"):
    """Tell capture-status.py how this stage went, if that script is installed. Never waits."""
    tool = os.path.join(HERE, "capture-status.py")
    if not os.path.isfile(tool):
        return
    try:
        subprocess.Popen([sys.executable, tool, "--dir", os.path.join(debrief_dir(), ".system"), "mark", stage, status, msg[:300]],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True)
    except Exception:
        pass


class Lock:
    """fcntl.flock on a lock file. The kernel drops it when the process dies, so no stale locks."""

    def __init__(self, path, wait=10.0):
        self.path, self.wait, self.fd = path, wait, None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        end = time.monotonic() + self.wait
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= end:
                    os.close(self.fd)
                    self.fd = None
                    raise GiveUp("lock busy: " + os.path.basename(self.path))
                time.sleep(0.02)

    def __exit__(self, *a):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)


def read_json(path, default):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, type(default)) else default
    except Exception:
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------- git


class Git:
    """Runs git with a per-call timeout and an optional overall deadline. Raises GiveUp on any failure."""

    CONFIG = [
        # Keep every write inside the private index and object store: no
        # shared index in .git, no fsmonitor or untracked cache, no auto gc.
        "-c", "core.splitIndex=false", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
        "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "core.hooksPath=/dev/null",
        "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false", "-c", "core.quotePath=false",
        "--literal-pathspecs", "--no-optional-locks",
    ]

    def __init__(self, deadline=None, per_call=CHILD_GIT_TIMEOUT):
        self.deadline, self.per_call = deadline, per_call
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1", LC_ALL="C")
        self.env = env

    def __call__(self, cwd, *args, extra=None, stdin=None, strip=True):
        timeout = self.per_call
        if self.deadline is not None:
            timeout = min(timeout, self.deadline - time.monotonic())
            if timeout < 0.02:
                raise GiveUp("time budget spent before git " + args[0])
        env = dict(self.env, **(extra or {}))
        try:
            r = subprocess.run(["git", "-C", cwd, *self.CONFIG, *args], env=env, input=stdin,
                               capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise GiveUp("git %s timed out" % args[0])
        except Exception as e:
            raise GiveUp("git %s: %s" % (args[0], e))
        if r.returncode != 0:
            raise GiveUp("git %s exit %d: %s" % (args[0], r.returncode, r.stderr[:200].decode("utf-8", "replace").strip()))
        out = r.stdout.decode("utf-8", "replace")
        return out.strip() if strip else out


def repo_info(git, cwd):
    """The work tree that contains cwd, or None. Asks git for paths: a worktree's .git is a file."""
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        out = git(cwd, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-path", "objects").splitlines()
    except GiveUp:
        return None
    if len(out) != 2:
        return None
    top, objects = out
    try:
        head = git(top, "rev-parse", "-q", "--verify", "HEAD^{commit}")
    except GiveUp:
        head = ""  # an unborn branch
    real = os.path.realpath(top)
    return {
        "top": top, "objects": os.path.realpath(objects), "head": head,
        "key": hashlib.sha1(real.encode()).hexdigest()[:12],
        "okey": hashlib.sha1(os.path.realpath(objects).encode()).hexdigest()[:12],
    }


def private_env(info, index=None):
    objs = store("objects", info["okey"])
    for sub in ("info", "pack"):
        os.makedirs(os.path.join(objs, sub), exist_ok=True)
    try:
        os.utime(objs)  # a store in use is never pruned
    except OSError:
        pass
    env = {"GIT_OBJECT_DIRECTORY": objs, "GIT_ALTERNATE_OBJECT_DIRECTORIES": info["objects"]}
    if index:
        env["GIT_INDEX_FILE"] = index
    return env


def snapshot(git, info, index_name):
    """Tree of HEAD with every changed, untracked or deleted design file laid over it.

    Listing reads the repo's own index (read-only). The tree is built in a
    private index file; new blobs and trees go to the private object store.
    """
    top = info["top"]
    listed = git(top, "ls-files", "-z", "-m", "-d", "-o", "--exclude-standard", strip=False)
    paths = set(p for p in listed.split("\0") if p)
    if info["head"]:
        staged = git(top, "diff-index", "--cached", "--name-only", "-z", "--no-renames", "HEAD", strip=False)
        paths.update(p for p in staged.split("\0") if p)
    cands = sorted(p for p in paths if is_design(p))
    if not cands and info["head"]:
        return git(top, "rev-parse", info["head"] + "^{tree}")
    index = store("index", index_name)
    os.makedirs(os.path.dirname(index), exist_ok=True)
    env = private_env(info, index)
    if info["head"]:
        git(top, "read-tree", info["head"], extra=env)
    else:
        git(top, "read-tree", "--empty", extra=env)
    if cands:
        git(top, "update-index", "--add", "--remove", "-z", "--stdin",
            extra=env, stdin=("\0".join(cands) + "\0").encode())
    return git(top, "write-tree", extra=env)


def diff_trees(git, info, base, head):
    """([{path, added, removed}], patch text) for design files only."""
    env = private_env(info)
    raw = git(info["top"], "diff-tree", "-r", "-z", "--numstat", "--no-renames", base, head, extra=env, strip=False)
    files, parts = [], raw.split("\0")
    for item in parts:
        bits = item.split("\t", 2)
        if len(bits) != 3 or not is_design(bits[2]):
            continue
        a, r, p = bits
        files.append({"path": p, "added": int(a) if a.isdigit() else None, "removed": int(r) if r.isdigit() else None})
    if not files:
        return [], ""
    paths = [f["path"] for f in files[:MAX_PATHSPEC]]
    patch = git(info["top"], "diff-tree", "-r", "-p", "--no-color", "--no-ext-diff", "--no-textconv",
                "--no-renames", "--src-prefix=a/", "--dst-prefix=b/", base, head, "--", *paths,
                extra=env, strip=False)
    return files, patch


def cut(text, limit):
    """Text cut at a line boundary under limit bytes, and the full size when cut (else None)."""
    data = text.encode("utf-8", "replace")
    if len(data) <= limit:
        return text, None
    head = data[:limit]
    nl = head.rfind(b"\n")
    if nl > 0:
        head = head[: nl + 1]
    return head.decode("utf-8", "ignore"), len(data)


# ---------------------------------------------------------------- state


def safe_sid(sid):
    return isinstance(sid, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,128}", sid) is not None


def state_path(sid, info):
    return store("state", "%s-%s.json" % (sid, info["key"]))


def state_lock(sid, info, wait):
    return Lock(store("state", "%s-%s.lock" % (sid, info["key"])), wait)


def marker_touch(sid, info, open_turn):
    """Record that this session ran a hook in this repo. Returns the other sessions' entries."""
    path = store("repos", info["key"] + ".json")
    with Lock(store("repos", info["key"] + ".lock"), 0.2):
        m = read_json(path, {})
        sess = m.get("sessions") if isinstance(m.get("sessions"), dict) else {}
        cutoff = time.time() - MARKER_DAYS * 86400
        sess = {k: v for k, v in sess.items() if isinstance(v, dict) and v.get("t", 0) >= cutoff}
        others = {k: v for k, v in sess.items() if k != sid}
        sess[sid] = {"t": time.time(), "open": open_turn}
        write_json(path, {"top": info["top"], "sessions": sess})
    return others


def material(sid, *parts):
    return os.path.join(debrief_dir(), "sessions", "material", sid[:8], "turns", *parts)


def record(sid, info, kind, base, head, entry_extra, files, patch):
    """Write one patch and its index line. Returns the line written (or None)."""
    entry = {"kind": kind, "repo": info["top"], "base": base, "head": head,
             "head_commit": info["head"] or None, "at": now_iso()}
    entry.update(entry_extra)
    red = redactor()
    with Lock(store("locks", sid[:8] + ".lock"), 30):
        idx = material(sid, "index.jsonl")
        n = 1
        if os.path.exists(idx):
            for ln in open(idx, errors="replace"):
                try:
                    if json.loads(ln).get("file"):
                        n += 1
                except Exception:
                    continue
        if red is None:
            # Fail closed: no redactor, no text. Not even the file names.
            entry.update(n=None, file=None, status="no_redactor", files=[], files_truncated=len(files),
                         patch_truncated=None)
        else:
            raw_size = len(patch.encode("utf-8", "replace"))
            pre, _ = cut(patch, MAX_PATCH * 4)  # bound the redactor's work on a huge patch
            text, _ = red(pre)
            text, was = cut(text, MAX_PATCH)
            truncated = raw_size if (was or raw_size > len(pre.encode("utf-8", "replace"))) else None
            if truncated:
                text += "# [debrief: patch cut here; the full patch was %d bytes]\n" % raw_size
            name = "%03d-%s.patch" % (n, kind)
            os.makedirs(material(sid), exist_ok=True)
            with open(material(sid, name), "w") as f:
                f.write(text)
            shown = []
            for fe in files[:MAX_FILES]:
                p, _ = red(fe["path"])
                shown.append(dict(fe, path=p))
            entry.update(n=n, file="turns/" + name, status="ok", files=shown,
                         files_truncated=len(files) if len(files) > MAX_FILES else None,
                         patch_truncated=truncated)
        os.makedirs(material(sid), exist_ok=True)
        with open(idx, "a") as f:
            f.write(json.dumps(entry) + "\n")
    return entry


def record_status(sid, info, kind, status, head, extra=None):
    entry = {"n": None, "kind": kind, "file": None, "status": status, "repo": info["top"],
             "base": None, "head": head, "head_commit": info["head"] or None, "at": now_iso()}
    entry.update(extra or {})
    with Lock(store("locks", sid[:8] + ".lock"), 30):
        os.makedirs(material(sid), exist_ok=True)
        with open(material(sid, "index.jsonl"), "a") as f:
            f.write(json.dumps(entry) + "\n")
    return entry


def background_running(transcript):
    """True when a task the agent started in the background has no completion notice yet.

    A heuristic over an unstable format: a tool call with run_in_background, or
    an agent call whose result says it was launched async, counts as started;
    a later <tool-use-id> in a task notification counts as done. None when the
    transcript cannot be read.
    """
    if not transcript or not os.path.isfile(transcript):
        return None
    started, done = set(), set()
    try:
        with open(transcript, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if size > MAX_TRANSCRIPT_SCAN:
                fh.seek(size - MAX_TRANSCRIPT_SCAN)
                fh.readline()
            for raw in fh:
                line = raw.decode("utf-8", "replace")
                if "<tool-use-id>" in line:
                    done.update(re.findall(r"<tool-use-id>([A-Za-z0-9_-]+)</tool-use-id>", line))
                if "run_in_background" not in line and "isAsync" not in line and "Async agent launched" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                msg = obj.get("message") if isinstance(obj, dict) else None
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                tur = obj.get("toolUseResult") if isinstance(obj.get("toolUseResult"), dict) else {}
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use" and isinstance(b.get("input"), dict) \
                            and b["input"].get("run_in_background") is True and b.get("id"):
                        started.add(b["id"])
                    elif b.get("type") == "tool_result" and b.get("tool_use_id"):
                        text = json.dumps(b.get("content"))[:300]
                        if tur.get("isAsync") is True or "Async agent launched" in text:
                            started.add(b["tool_use_id"])
    except Exception:
        return None
    return bool(started - done)


# ---------------------------------------------------------------- events


def on_prompt(hook):
    """UserPromptSubmit: snapshot now (synchronous, under budget), then queue a between patch."""
    start = time.monotonic()
    git = Git(deadline=start + UPS_BUDGET, per_call=0.5)
    sid, cwd = hook["session_id"], hook.get("cwd") or os.getcwd()
    info = repo_info(git, cwd)
    if info is None:
        return
    with state_lock(sid, info, 0.2):
        st = read_json(state_path(sid, info), {})
        try:
            tree = snapshot(git, info, "%s-%s.idx" % (sid, info["key"]))
        except GiveUp as e:
            st.update(start_tree=None, agent_base=None, start_failed=str(e)[:200], stop_seen=False,
                      prompts=st.get("prompts", 0) + 1, top=info["top"], open=True)
            write_json(state_path(sid, info), st)
            log("prompt %s %s: no snapshot: %s" % (sid[:8], info["top"], e))
            mark("fail", "no snapshot at prompt: %s" % e)
            return
        job = None
        base = st.get("last_stop_tree") if st.get("stop_seen") else st.get("start_tree")
        if base and base != tree:
            doubts = []
            if not st.get("stop_seen"):
                doubts.append("interrupted_turn")
            if (st.get("last_stop_head") if st.get("stop_seen") else st.get("start_head")) != info["head"]:
                doubts.append("head_moved")
            since = st.get("last_stop_t") if st.get("stop_seen") else st.get("start_t")
            try:
                others = marker_touch(sid, info, True)
            except GiveUp:
                others = {}
            if any(v.get("open") or v.get("t", 0) > (since or 0) for v in others.values()):
                doubts.append("other_session")
            if st.get("stop_seen") and st.get("bg_running"):
                doubts.append("background_task")
            job = {"op": "between", "sid": sid, "info": info, "base": base, "head": tree, "doubts": doubts}
        else:
            try:
                marker_touch(sid, info, True)
            except GiveUp:
                pass
        st.update(top=info["top"], start_tree=tree, start_head=info["head"], start_t=time.time(),
                  agent_base=tree, stop_seen=False, start_failed=None, open=True,
                  prompts=st.get("prompts", 0) + 1)
        write_json(state_path(sid, info), st)
    if job:
        spawn(job)


def on_stop(hook):
    """Stop: hand the work to a detached child and return."""
    spawn({"op": "stop", "sid": hook["session_id"], "cwd": hook.get("cwd") or os.getcwd(),
           "transcript": hook.get("transcript_path") or ""})


def spawn(job):
    if enabled("DEBRIEF_TURNS_SYNC"):  # tests: run the child's work inline
        child(job)
        return
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--child", json.dumps(job)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True, cwd="/")


def child(job):
    try:
        if job["op"] == "between":
            git = Git()
            files, patch = diff_trees(git, job["info"], job["base"], job["head"])
            if files:
                e = record(job["sid"], job["info"], "between", job["base"], job["head"],
                           {"attribution": "unverified", "doubts": job["doubts"]}, files, patch)
                mark("ok", "between patch %s (%d files)" % (e.get("file"), len(files)))
        elif job["op"] == "stop":
            stop_work(job)
    except Exception as e:
        log("child %s failed: %s" % (job.get("op"), e))
        mark("fail", "%s: %s" % (job.get("op"), e))


def stop_work(job):
    git = Git()
    sid = job["sid"]
    bg = background_running(job.get("transcript"))
    # The cwd's repo, plus any repo this session opened a turn in.
    infos = {}
    info = repo_info(git, job.get("cwd"))
    if info:
        infos[info["key"]] = info
    sdir = store("state")
    for name in os.listdir(sdir) if os.path.isdir(sdir) else []:
        if name.startswith(sid + "-") and name.endswith(".json"):
            st = read_json(os.path.join(sdir, name), {})
            if st.get("open") and st.get("top"):
                i = repo_info(git, st["top"])
                if i and i["key"] not in infos:
                    infos[i["key"]] = i
    for info in infos.values():
        try:
            stop_repo(git, sid, info, bg)
        except Exception as e:
            log("stop %s %s: %s" % (sid[:8], info["top"], e))
            mark("fail", "stop: %s" % e)


def stop_repo(git, sid, info, bg):
    with state_lock(sid, info, 30):
        st = read_json(state_path(sid, info), {})
        tree = snapshot(git, info, "%s-%s.idx" % (sid, info["key"]))
        base = st.get("agent_base")
        start_failed = st.get("start_failed")
        st.update(top=info["top"], last_stop_tree=tree, last_stop_head=info["head"], last_stop_t=time.time(),
                  stop_seen=True, agent_base=tree, open=False, bg_running=bool(bg))
        write_json(state_path(sid, info), st)
    try:
        others = marker_touch(sid, info, False)
    except GiveUp:
        others = {}
    if not base:
        why = "no start snapshot for this turn" + (" (%s)" % start_failed if start_failed else "")
        record_status(sid, info, "agent", "no_start_snapshot", tree, {"note": why})
        mark("fail", why)
        return
    if base == tree:
        mark("ok", "turn changed no design file")
        return
    files, patch = diff_trees(git, info, base, tree)
    if not files:
        mark("ok", "turn changed no design file")
        return
    extra = {}
    if any(v.get("open") for v in others.values()):
        extra["doubts"] = ["other_session"]
    e = record(sid, info, "agent", base, tree, extra, files, patch)
    mark("ok", "agent patch %s (%d files)" % (e.get("file"), len(files)))
    web = [f["path"] for f in files if is_web(f["path"])]
    if web and e.get("n") and enabled("DEBRIEF_SCREENS"):
        tool = os.path.join(HERE, "screen-capture.py")
        if os.path.isfile(tool):
            out = os.path.join(debrief_dir(), "sessions", "material", sid[:8], "screens")
            cmd = [sys.executable, tool, "run", "--quiet", "--cwd", info["top"], "--out", out,
                   "--session", sid, "--turn", str(e["n"]), "--tree", tree]
            for p in web[:200]:
                cmd += ["--changed", p]
            try:
                subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=120)
            except Exception as ex:
                log("screens: %s" % ex)


def cleanup(sid):
    """Drop this session's private index files and locks; prune stores unused for PRUNE_DAYS."""
    if safe_sid(sid):
        for sub in ("index", "state"):
            d = store(sub)
            for name in os.listdir(d) if os.path.isdir(d) else []:
                if name.startswith(sid + "-") and (name.endswith(".idx") or name.endswith(".lock")
                                                   or name.endswith(".idx.lock")):
                    try:
                        os.remove(os.path.join(d, name))
                    except OSError:
                        pass
    cutoff = time.time() - PRUNE_DAYS * 86400
    for sub in ("objects", "state", "index"):
        d = store(sub)
        for name in os.listdir(d) if os.path.isdir(d) else []:
            p = os.path.join(d, name)
            try:
                if os.path.getmtime(p) < cutoff:
                    shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            except OSError:
                pass


# ---------------------------------------------------------------- entry


def run_hook(event, raw):
    if not enabled("DEBRIEF_TURNS"):
        return
    try:
        hook = json.loads(raw) if raw.strip() else {}
    except Exception:
        return
    if not isinstance(hook, dict) or not safe_sid(hook.get("session_id")):
        return
    if event == "UserPromptSubmit":
        on_prompt(hook)
    elif event == "Stop":
        on_stop(hook)
    elif event == "SessionEnd":
        cleanup(hook["session_id"])


def main():
    a = sys.argv[1:]
    if a == ["--selftest"]:
        sys.exit(selftest())
    if len(a) == 2 and a[0] == "--child":
        sys.stdout = open(os.devnull, "w")
        try:
            child(json.loads(a[1]))
        except Exception as e:
            log("child: %s" % e)
        return
    if len(a) == 3 and a[0] == "cleanup" and a[1] == "--session":
        cleanup(a[2])
        return
    if len(a) == 2 and a[0] == "--event":
        # A hook. Nothing may reach stdout, and nothing may raise.
        real = sys.stdout
        sys.stdout = open(os.devnull, "w")
        try:
            raw = sys.stdin.read(4 * 1024 * 1024) if not sys.stdin.isatty() else ""
            run_hook(a[1], raw)
        except BaseException as e:  # noqa: B902 - a hook must never fail the session
            log("hook %s: %r" % (a[1], e))
        finally:
            sys.stdout = real
        os._exit(0)
    sys.stderr.write(__doc__.split("Usage")[1])
    sys.exit(2)


# ---------------------------------------------------------------- selftest


def selftest():
    import tempfile

    fails = []

    def check(name, ok, detail=""):
        print(("ok " if ok else "FAIL ") + name + (": %s" % (detail,) if detail and not ok else ""))
        if not ok:
            fails.append(name)

    td = tempfile.mkdtemp(prefix="turns-selftest-")
    try:
        return _selftest(td, check, fails)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def _selftest(td, check, fails):
    me = os.path.abspath(__file__)
    ddir = os.path.join(td, "debrief")
    os.makedirs(ddir)
    genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_NOSYSTEM="1", HOME=td)
    for k in [k for k in genv if k.startswith("GIT_DIR") or k == "GIT_INDEX_FILE"]:
        genv.pop(k)

    def sh(cwd, *args):
        return subprocess.run(["git", "-C", cwd, *args], env=genv, capture_output=True, text=True)

    def w(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def hook(event, sid, cwd, env_extra=None, raw=None, script=me, transcript=""):
        env = dict(os.environ, DEBRIEF_DIR=ddir, DEBRIEF_TURNS="1", DEBRIEF_TURNS_SYNC="1", HOME=td)
        env.pop("DEBRIEF_SCREENS", None)
        env.update(env_extra or {})
        body = raw if raw is not None else json.dumps({
            "session_id": sid, "cwd": cwd, "transcript_path": transcript, "hook_event_name": event})
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, script, "--event", event], input=body.encode(), env=env,
                           capture_output=True, timeout=60)
        return r, time.monotonic() - t0

    outs = []

    def run(event, sid, cwd, **kw):
        r, dt = hook(event, sid, cwd, **kw)
        outs.append((event, r.returncode, r.stdout))
        return r, dt

    def index(sid):
        p = os.path.join(ddir, "sessions", "material", sid[:8], "turns", "index.jsonl")
        if not os.path.exists(p):
            return []
        return [json.loads(x) for x in open(p) if x.strip()]

    def patch_text(sid, e):
        return open(os.path.join(ddir, "sessions", "material", sid[:8], e["file"])).read()

    def tree_digest(root):
        h = hashlib.sha256()
        for dp, dn, fn in sorted(os.walk(root)):
            dn.sort()
            for f in sorted(fn):
                p = os.path.join(dp, f)
                h.update(p.encode())
                with open(p, "rb") as fh:
                    h.update(fh.read())
        return h.hexdigest()

    # A repo with a committed component and a server file.
    repo = os.path.join(td, "repo")
    os.makedirs(repo)
    sh(repo, "init", "-q", "-b", "main")
    w(os.path.join(repo, "src", "Card.tsx"), "export const Card = () => <div>v1</div>\n")
    w(os.path.join(repo, "src", "Gone.css"), ".gone { color: red }\n")
    w(os.path.join(repo, "server", "db.py"), "x = 1\n")
    w(os.path.join(repo, "app", "[id]", "page.tsx"), "export default function P() { return null }\n")
    sh(repo, "add", ".")
    sh(repo, "commit", "-qm", "v1")
    gitdir_before = tree_digest(os.path.join(repo, ".git"))

    S1 = "aaaa1111-0000-0000-0000-000000000001"
    run("UserPromptSubmit", S1, repo)
    # The "agent" works: modify, add untracked, delete, touch excluded files.
    w(os.path.join(repo, "src", "Card.tsx"), "export const Card = () => <div>v2</div>\n")
    w(os.path.join(repo, "src", "New.svelte"), "<p>new</p>\n")
    os.remove(os.path.join(repo, "src", "Gone.css"))
    w(os.path.join(repo, "ios", "Home.swift"), "struct Home {}\n")
    w(os.path.join(repo, ".env"), "API_KEY=supersecretvalue123\n")
    w(os.path.join(repo, "package-lock.json"), "{}\n")
    w(os.path.join(repo, "server", "db.py"), "x = 2\n")
    w(os.path.join(repo, "app", "[id]", "page.tsx"), "export default function P() { return 1 }\n")
    secret = "sk-ant-api03-" + "B" * 40
    w(os.path.join(repo, "src", "Key.tsx"), "const k = '%s'\n" % secret)
    run("Stop", S1, repo)
    ix = index(S1)
    agent = [e for e in ix if e["kind"] == "agent" and e.get("file")]
    check("agent: one patch for the turn", len(agent) == 1, ix)
    paths = {f["path"] for f in agent[0]["files"]} if agent else set()
    check("agent: a modified, an untracked and a deleted design file are in it",
          {"src/Card.tsx", "src/New.svelte", "src/Gone.css"} <= paths, paths)
    check("agent: a .swift file is included", "ios/Home.swift" in paths, paths)
    check("agent: a path with brackets is read literally", "app/[id]/page.tsx" in paths, paths)
    check("agent: .env, a lockfile and a server file are excluded",
          not paths & {".env", "package-lock.json", "server/db.py"}, paths)
    text = patch_text(S1, agent[0]) if agent else ""
    check("agent: the patch has the new line", "+export const Card = () => <div>v2</div>" in text)
    check("agent: a secret-shaped value is redacted", secret not in text and "[REDACTED:" in text)
    check("agent: no .env text reaches the patch", "supersecretvalue123" not in text)
    check("agent: counts recorded", agent and {"path": "src/Card.tsx", "added": 1, "removed": 1} in agent[0]["files"])
    check("agent: head_commit and trees recorded",
          agent and len(agent[0]["head_commit"] or "") == 40 and agent[0]["base"] != agent[0]["head"])
    check("the user's .git (index and objects) is byte-identical", tree_digest(os.path.join(repo, ".git")) == gitdir_before)

    # An unchanged turn writes nothing.
    n0 = len(index(S1))
    run("UserPromptSubmit", S1, repo)
    run("Stop", S1, repo)
    check("an unchanged turn writes nothing", len(index(S1)) == n0, index(S1)[n0:])

    # An edit between Stop and the next prompt is a between patch, unverified.
    w(os.path.join(repo, "src", "Card.tsx"), "export const Card = () => <div>human</div>\n")
    run("UserPromptSubmit", S1, repo)
    ix = index(S1)
    between = [e for e in ix if e["kind"] == "between"]
    check("between: an idle-time edit lands in a between patch", len(between) == 1 and between[0]["file"].endswith("-between.patch"), ix)
    check("between: attribution is unverified", between and between[0]["attribution"] == "unverified")
    check("between: a clean gap carries no doubts", between and between[0]["doubts"] == [], between)
    check("between: numbering continues across kinds", between and between[0]["n"] == 2, between)

    # A prompt with no Stop before it (an interrupted turn) records the doubt.
    w(os.path.join(repo, "src", "Card.tsx"), "export const Card = () => <div>half</div>\n")
    run("UserPromptSubmit", S1, repo)
    b2 = [e for e in index(S1) if e["kind"] == "between"]
    check("between: no Stop for the previous prompt is a doubt", len(b2) == 2 and "interrupted_turn" in b2[-1]["doubts"], b2)
    run("Stop", S1, repo)

    # HEAD moved, and another session active in the gap.
    S2 = "bbbb2222-0000-0000-0000-000000000002"
    w(os.path.join(repo, "src", "Other.css"), ".o{}\n")
    sh(repo, "add", "src/Other.css")
    sh(repo, "commit", "-qm", "other")
    run("UserPromptSubmit", S2, repo)
    run("UserPromptSubmit", S1, repo)
    b3 = [e for e in index(S1) if e["kind"] == "between"]
    check("between: HEAD moved and another session are doubts",
          b3 and {"head_moved", "other_session"} <= set(b3[-1]["doubts"]), b3[-1:] if b3 else b3)

    # A background task still running at Stop is a doubt on the next gap.
    tr = os.path.join(td, "t.jsonl")
    w(tr, json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_bg1", "name": "Bash", "input": {"command": "sleep 99", "run_in_background": True}}]}}) + "\n")
    run("Stop", S1, repo, transcript=tr)
    w(os.path.join(repo, "src", "Card.tsx"), "export const Card = () => <div>bg</div>\n")
    run("UserPromptSubmit", S1, repo)
    b4 = [e for e in index(S1) if e["kind"] == "between"]
    check("between: a background task still running is a doubt", "background_task" in b4[-1]["doubts"], b4[-1])
    with open(tr, "a") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content":
                "<task-notification><tool-use-id>toolu_bg1</tool-use-id></task-notification>"}}) + "\n")
    check("background: a completion notice clears it", background_running(tr) is False)

    # Stop with no start snapshot says so.
    S3 = "cccc3333-0000-0000-0000-000000000003"
    run("Stop", S3, repo)
    i3 = index(S3)
    check("stop with no start snapshot is recorded", len(i3) == 1 and i3[0]["status"] == "no_start_snapshot"
          and i3[0]["file"] is None, i3)

    # Long patch and many files are cut and counted.
    S4 = "dddd4444-0000-0000-0000-000000000004"
    run("UserPromptSubmit", S4, repo)
    for i in range(MAX_FILES + 5):
        w(os.path.join(repo, "many", "f%03d.css" % i), ".a{}\n" * 10)
    w(os.path.join(repo, "big", "Big.html"), "<p>line</p>\n" * 40000)
    run("Stop", S4, repo)
    a4 = [e for e in index(S4) if e.get("file")]
    check("a long patch is cut at a line boundary and its full size kept",
          a4 and a4[0]["patch_truncated"] and a4[0]["patch_truncated"] > MAX_PATCH
          and len(patch_text(S4, a4[0]).encode()) < MAX_PATCH + 200
          and patch_text(S4, a4[0]).endswith("bytes]\n"), a4[0]["patch_truncated"] if a4 else a4)
    check("more than N files: N listed, full count kept",
          a4 and len(a4[0]["files"]) == MAX_FILES and a4[0]["files_truncated"] == MAX_FILES + 6, a4 and a4[0]["files_truncated"])

    # A worktree: .git is a file there.
    wt = os.path.join(td, "wt")
    sh(repo, "worktree", "add", "-q", "-b", "wtb", wt)
    S5 = "eeee5555-0000-0000-0000-000000000005"
    run("UserPromptSubmit", S5, wt)
    w(os.path.join(wt, "src", "Card.tsx"), "export const Card = () => <div>wt</div>\n")
    run("Stop", S5, wt)
    a5 = [e for e in index(S5) if e.get("file")]
    check("worktree: the patch is written", a5 and os.path.realpath(a5[0]["repo"]) == os.path.realpath(wt)
          and "<div>wt</div>" in patch_text(S5, a5[0]), a5)

    # Outside a repo, and switched off: nothing at all.
    plain = os.path.join(td, "plain")
    os.makedirs(plain)
    S6 = "ffff6666-0000-0000-0000-000000000006"
    run("UserPromptSubmit", S6, plain)
    run("Stop", S6, plain)
    check("outside a repo nothing is written", not index(S6)
          and not os.path.exists(os.path.join(ddir, "sessions", "material", S6[:8])))
    S7 = "abab7777-0000-0000-0000-000000000007"
    for off in ({"DEBRIEF_TURNS": "0"}, {"DEBRIEF_TURNS": ""}):
        run("UserPromptSubmit", S7, repo, env_extra=off)
        w(os.path.join(repo, "src", "Card.tsx"), "export const Card = () => <div>off</div>\n")
        run("Stop", S7, repo, env_extra=off)
    sdir = os.path.join(ddir, ".system", "turns", "state")
    check("switched off nothing is written", not index(S7) and not [x for x in os.listdir(sdir) if x.startswith(S7)])

    # Garbage stdin and a failing or slow git: exit 0, no output, under a second.
    for raw in ("", "not json", "[1,2]", '{"session_id": "../../x"}', "\x00\xff"):
        r, _ = run("UserPromptSubmit", "", repo, raw=raw)
    fake = os.path.join(td, "fakebin")
    w(os.path.join(fake, "git"), "#!/bin/sh\nexit 128\n")
    w(os.path.join(fake, "slow", "git"), "#!/bin/sh\nsleep 5\n")
    os.chmod(os.path.join(fake, "git"), 0o755)
    os.chmod(os.path.join(fake, "slow", "git"), 0o755)
    S8 = "acac8888-0000-0000-0000-000000000008"
    r, _ = run("UserPromptSubmit", S8, repo, env_extra={"PATH": fake + ":" + os.environ["PATH"]})
    r2, _ = run("Stop", S8, repo, env_extra={"PATH": fake + ":" + os.environ["PATH"]})
    check("a failing git never raises", r.returncode == 0 and r2.returncode == 0 and not r.stderr and not r2.stderr,
          (r.stderr[-300:], r2.stderr[-300:]))
    r, dt = run("UserPromptSubmit", S8, repo, env_extra={"PATH": os.path.join(fake, "slow") + ":" + os.environ["PATH"]})
    check("a slow git gives up under a second", r.returncode == 0 and dt < 1.0, dt)
    _, dt = run("UserPromptSubmit", S1, repo)
    check("UserPromptSubmit on a small repo is under a second", dt < 1.0, dt)

    # The real detached child (no SYNC): the hook returns at once, the patch arrives.
    S9 = "adad9999-0000-0000-0000-000000000009"
    run("UserPromptSubmit", S9, repo, env_extra={"DEBRIEF_TURNS_SYNC": "0"})
    w(os.path.join(repo, "src", "Card.tsx"), "export const Card = () => <div>detached</div>\n")
    _, dt = run("Stop", S9, repo, env_extra={"DEBRIEF_TURNS_SYNC": "0"})
    end = time.time() + 15
    while time.time() < end and not [e for e in index(S9) if e.get("file")]:
        time.sleep(0.1)
    check("detached Stop: the patch arrives after the hook returned",
          bool([e for e in index(S9) if e.get("file")]), index(S9))

    # No redactor: no patch text, no file names.
    sysdir = os.path.join(td, "noredact", ".system")
    os.makedirs(sysdir)
    shutil.copy(me, os.path.join(sysdir, "turn-capture.py"))
    SA = "aeae0000-0000-0000-0000-00000000000a"
    run("UserPromptSubmit", SA, repo, script=os.path.join(sysdir, "turn-capture.py"))
    w(os.path.join(repo, "src", "Card.tsx"), "export const Card = () => <div>nored</div>\n")
    run("Stop", SA, repo, script=os.path.join(sysdir, "turn-capture.py"))
    ia = index(SA)
    mdir = os.path.join(ddir, "sessions", "material", SA[:8], "turns")
    check("no redactor: no patch text is written", ia and ia[0]["status"] == "no_redactor" and not ia[0]["files"]
          and not [x for x in os.listdir(mdir) if x.endswith(".patch")], ia)

    check("stdout is empty on every event", all(not o for _, _, o in outs),
          [(e, o[:80]) for e, _, o in outs if o])
    check("every hook exits 0", all(c == 0 for _, c, _ in outs), [(e, c) for e, c, _ in outs if c])
    check("the user's .git objects were never written by the hook",
          not os.path.exists(os.path.join(repo, ".git", "index.lock")))

    # SessionEnd cleanup removes the private index files.
    idx_files = lambda sid: [x for x in os.listdir(os.path.join(ddir, ".system", "turns", "index")) if x.startswith(sid)]
    had = bool(idx_files(S1))
    run("SessionEnd", S1, repo)
    check("cleanup removes the session's private index", had and not idx_files(S1) and bool(idx_files(S4)))
    old = os.path.join(ddir, ".system", "turns", "objects", "oldstore000")
    os.makedirs(old)
    t = time.time() - (PRUNE_DAYS + 1) * 86400
    os.utime(old, (t, t))
    run("SessionEnd", S4, repo)
    check("cleanup prunes a private store older than 14 days", not os.path.exists(old))
    check("patches outlive the private store", os.path.exists(os.path.join(ddir, "sessions", "material", S1[:8], agent[0]["file"])) if agent else False)

    print("ALL PASS" if not fails else "%d FAILED" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    main()
