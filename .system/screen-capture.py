#!/usr/bin/env python3
"""screen-capture — a picture of the work, taken at Stop from the dev server the repo already runs.

turn-capture.py calls this from its detached Stop child when an agent patch
touched web design files. It can also be run by hand. It decides whether to
take pictures and what of; screen-capture.mjs takes them.

    sessions/material/<sid8>/screens/
      <NNN>-<HHMMSS>-<desktop|phone>-<route slug>.png
      index.jsonl      one line per picture, or one status line when none was taken

Rules:
  * Never start a server. Use one this repo started: a listening TCP port whose
    process runs with its cwd inside the repo, and whose / answers with HTML.
    `.debrief/runtime.json` in the repo ({"url": ..., "routes": [...]}) wins.
    Only loopback URLs are accepted.
  * Never download. Use a Playwright that is already installed, with its
    browser already in the cache. Else record "no_browser" and the command.
  * Read-only: see screen-capture.mjs and docs/turns.md for what that covers
    and what it cannot.
  * One capture per repo at a time. A newer request replaces a queued one.
  * Images cannot be redacted. A page that shows a secret keeps it.

Usage:
    screen-capture.py run --cwd DIR --out DIR [--url URL] [--route /x ...]
                          [--session ID] [--turn N] [--tree T] [--changed PATH ...]
                          [--force] [--quiet]
    screen-capture.py --selftest

Off unless DEBRIEF_SCREENS=1.
"""

import argparse
import datetime
import fcntl
import glob
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MJS = os.path.join(HERE, "screen-capture.mjs")
MAX_ROUTES = 3
MAX_HEIGHT = 6000
KILL_AFTER = 45
LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
ROUTE_EXT = {".tsx", ".jsx", ".ts", ".js", ".mdx", ".md"}
NEXT_CONFIGS = ("next.config.js", "next.config.mjs", "next.config.ts", "next.config.cjs", "next.config.mts")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def debrief_dir():
    return os.environ.get("DEBRIEF_DIR") or os.path.dirname(HERE)


def store(*parts):
    return os.path.join(debrief_dir(), ".system", "turns", "screens", *parts)


def turns_module():
    """turn-capture.py, for its git snapshot and its redactor loader. None when missing."""
    try:
        spec = importlib.util.spec_from_file_location("turn_capture", os.path.join(HERE, "turn-capture.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def redactor():
    try:
        spec = importlib.util.spec_from_file_location("redact_transcript", os.path.join(HERE, "redact-transcript.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.redact("probe")
        return mod.redact
    except Exception:
        return None


def mark(status, msg):
    tool = os.path.join(HERE, "capture-status.py")
    if not os.path.isfile(tool):
        return
    try:
        subprocess.run([sys.executable, tool, "--dir", os.path.join(debrief_dir(), ".system"), "mark", "screens", status, msg[:300]],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass


def run_quiet(cmd, timeout):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


# ---------------------------------------------------------------- the dev server


def listeners():
    """{pid: set(ports)} for listening TCP sockets, from lsof."""
    out = run_quiet(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn"], 8)
    found, pid = {}, None
    for line in out.splitlines():
        if line.startswith("p"):
            pid = int(line[1:]) if line[1:].isdigit() else None
        elif line.startswith("n") and pid:
            m = re.search(r":(\d+)$", line)
            if m:
                found.setdefault(pid, set()).add(int(m.group(1)))
    return found


def process_cwds(pids):
    if not pids:
        return {}
    out = run_quiet(["lsof", "-a", "-d", "cwd", "-p", ",".join(str(p) for p in pids), "-Fpn"], 8)
    cwds, pid = {}, None
    for line in out.splitlines():
        if line.startswith("p"):
            pid = int(line[1:]) if line[1:].isdigit() else None
        elif line.startswith("n") and pid:
            cwds[pid] = line[1:]
    return cwds


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


# No proxy: a proxy setting must never carry a request off the machine.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def answers_html(base, timeout=3.0):
    """True when GET / answers with HTML (one same-origin redirect is followed)."""
    url = base.rstrip("/") + "/"
    for _ in range(2):
        try:
            r = _OPENER.open(urllib.request.Request(url, headers={"Accept": "text/html"}), timeout=timeout)
            ctype = r.headers.get("Content-Type", "")
            body = r.read(2048).lstrip().lower()
            return "html" in ctype or body.startswith(b"<!doctype") or body.startswith(b"<html")
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location") if 300 <= e.code < 400 else None
            if not loc:
                ctype = e.headers.get("Content-Type", "")
                return "html" in ctype  # a 500 build error page is still the server's page
            nxt = urllib.parse.urljoin(url, loc)
            if origin(nxt) != origin(url):
                return True  # redirects off-origin (to a login host): it is a web server; do not follow
            url = nxt
        except Exception:
            return False
    return False


def origin(url):
    p = urllib.parse.urlsplit(url)
    return "%s://%s" % (p.scheme, p.netloc)


def is_loopback(url):
    try:
        p = urllib.parse.urlsplit(url)
        host = p.hostname or ""
    except Exception:
        return False
    return p.scheme in ("http", "https") and (host in LOOPBACK or host.startswith("127."))


def inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def discover(top):
    """Dev servers this repo started: [{url, port, pid, cwd}], best first."""
    lis = listeners()
    cwds = process_cwds(sorted(lis))
    found = []
    for pid, ports in lis.items():
        cwd = cwds.get(pid)
        if not cwd or not inside(cwd, top):
            continue
        for port in sorted(ports):
            for host in ("localhost", "127.0.0.1"):
                base = "http://%s:%d" % (host, port)
                if answers_html(base):
                    found.append({"url": base, "port": port, "pid": pid, "cwd": os.path.realpath(cwd)})
                    break
    # The deepest cwd first: in a monorepo it is the app, not the workspace root.
    found.sort(key=lambda s: (-len(s["cwd"]), s["port"]))
    return found


def runtime_override(top):
    p = os.path.join(top, ".debrief", "runtime.json")
    try:
        d = json.load(open(p))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


# ---------------------------------------------------------------- routes


def is_next_root(d):
    if any(os.path.isfile(os.path.join(d, c)) for c in NEXT_CONFIGS):
        return True
    try:
        pkg = json.load(open(os.path.join(d, "package.json")))
        deps = dict(pkg.get("dependencies") or {}, **(pkg.get("devDependencies") or {}))
        return "next" in deps
    except Exception:
        return False


def route_for(kind, segs):
    """The URL path for a Next.js file below app/ or pages/, or None when it has no fixed one."""
    if not segs:
        return None
    name, ext = os.path.splitext(segs[-1])
    if ext not in ROUTE_EXT:
        return None
    dirs, out = segs[:-1], []
    if kind == "app":
        if name not in ("page", "layout"):
            return None
        for s in dirs:
            if s.startswith("(."):
                return None  # an intercepting route
            if s.startswith("(") and s.endswith(")"):
                continue  # a route group adds nothing to the URL
            if s.startswith("@"):
                continue  # a parallel slot
            if s.startswith("_") or "[" in s:
                return None  # a private folder, or a dynamic segment
            out.append(s)
        return "/" + "/".join(out)
    if dirs and dirs[0] == "api":
        return None
    if name in ("_app", "_document"):
        return "/"
    if name.startswith("_") or "[" in name or any("[" in s or s.startswith("_") for s in dirs):
        return None
    return "/" + "/".join(dirs + ([] if name == "index" else [name]))


def nearest_next_root(d, top):
    """The Next.js project root that contains d (d itself or a parent, up to top), real path."""
    d, top = os.path.realpath(d), os.path.realpath(top)
    while inside(d, top):
        if is_next_root(d):
            return d
        if d == top:
            break
        d = os.path.dirname(d)
    return None


def routes_for(top, changed, server_cwd=None):
    """File-based routes for changed Next.js pages. Empty when no change maps to one."""
    routes = []
    server_root = nearest_next_root(server_cwd, top) if server_cwd else None
    for rel in changed:
        parts = [p for p in rel.replace("\\", "/").split("/") if p]
        for kind in ("app", "pages"):
            if kind not in parts[:-1]:
                continue
            i = parts.index(kind)
            proj = parts[:i - 1] if i and parts[i - 1] == "src" else parts[:i]
            projdir = os.path.join(top, *proj)
            # app/ or pages/ counts only at a Next.js project root (or its src/).
            if not is_next_root(projdir):
                continue
            if server_root and os.path.realpath(projdir) != server_root:
                continue  # another app in the same monorepo
            r = route_for(kind, parts[i + 1:])
            if r is not None and r not in routes:
                routes.append(r)
            break
    return routes


def manifest_routes(out_dir, base):
    """Paths of loopback links to this server already in the session's material manifest."""
    mp = os.path.join(os.path.dirname(os.path.abspath(out_dir)), "manifest.json")
    try:
        links = json.load(open(mp)).get("links") or []
    except Exception:
        return []
    want = urllib.parse.urlsplit(base)
    found = []
    for e in links:
        u = e.get("url") if isinstance(e, dict) else None
        if not isinstance(u, str) or "REDACTED" in u:
            continue
        p = urllib.parse.urlsplit(u)
        if p.port == want.port and (p.hostname or "") in LOOPBACK and p.path not in found:
            found.append(p.path or "/")
    return found


def slug(route):
    s = re.sub(r"[^A-Za-z0-9]+", "-", route.strip("/")).strip("-")[:60]
    return s or "root"


# ---------------------------------------------------------------- playwright


def node_bin():
    for c in (shutil.which("node"), "/opt/homebrew/bin/node", "/usr/local/bin/node"):
        if c and os.path.isfile(c):
            return c
    return None


def playwright_candidates(bases):
    """Playwright package dirs: the repo's own, then a global one, then DEBRIEF_PLAYWRIGHT."""
    out = []

    def add(d):
        if d and os.path.isfile(os.path.join(d, "package.json")) and d not in out:
            out.append(d)

    for b in bases:
        add(os.path.join(b, "node_modules", "playwright"))
        add(os.path.join(b, "node_modules", "@playwright", "test"))
    roots = []
    node = node_bin()
    if node:
        roots.append(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(node))), "lib", "node_modules"))
        roots.append(os.path.join(os.path.dirname(os.path.dirname(node)), "lib", "node_modules"))
    roots += ["/opt/homebrew/lib/node_modules", "/usr/local/lib/node_modules"]
    for r in roots:
        add(os.path.join(r, "playwright"))
        add(os.path.join(r, "@playwright", "test"))
        for d in sorted(glob.glob(os.path.join(r, "*", "node_modules", "playwright"))
                        + glob.glob(os.path.join(r, "@*", "*", "node_modules", "playwright"))):
            add(d)
    env = os.environ.get("DEBRIEF_PLAYWRIGHT", "").strip()
    if env:
        add(env)
        add(os.path.join(env, "playwright"))
        add(os.path.join(env, "@playwright", "test"))
    return out


def find_playwright(cands):
    """(the first install with Chromium in the cache, preferring one that also has WebKit; every probe)."""
    node = node_bin()
    tried, first = [], None
    if not node:
        return None, tried
    for d in cands:
        out = run_quiet([node, MJS, "probe", d], 20)
        try:
            p = json.loads(out.strip().splitlines()[-1])
        except Exception:
            p = {"pkg": d, "chromium": False, "error": "probe failed"}
        tried.append(p)
        if p.get("chromium") and p.get("webkit"):
            return p, tried
        if p.get("chromium") and first is None:
            first = p  # keep looking for one that also has WebKit, for the iPhone profile
    return first, tried


def install_hint(tried):
    if tried:
        return "node %s install chromium webkit" % os.path.join(tried[0]["pkg"], "cli.js")
    return "npm install -g playwright && playwright install chromium webkit"


# ---------------------------------------------------------------- index


def index_lines(out):
    p = os.path.join(out, "index.jsonl")
    if not os.path.exists(p):
        return []
    rows = []
    for ln in open(p, errors="replace"):
        try:
            rows.append(json.loads(ln))
        except Exception:
            continue
    return rows


def append(out, rows):
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "index.jsonl"), "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def status_line(out, status, req, **extra):
    """A line that says no picture was taken, and why. Not repeated back to back."""
    rows = index_lines(out)
    if rows and rows[-1].get("status") == status and not rows[-1].get("file"):
        return None
    row = {"n": None, "kind": "screen", "file": None, "route": None, "url": None, "viewport": None,
           "engine": None, "http_status": None, "build_error": None, "stale": None,
           "tree": req.get("tree"), "turn": req.get("turn"), "at": now_iso(), "status": status}
    row.update(extra)
    append(out, [row])
    return row


# ---------------------------------------------------------------- one capture


def design_tree(top):
    """A tree hash of the design files on disk now, or None."""
    tc = turns_module()
    if tc is None:
        return None
    try:
        git = tc.Git(per_call=20)
        info = tc.repo_info(git, top)
        return tc.snapshot(git, info, "screens-%s.idx" % info["key"]) if info else None
    except Exception:
        return None


def capture(req, pw_search=None):
    """Run one request. Returns the rows written (status rows included)."""
    top, out = req["top"], req["out"]
    tree = req.get("tree") or design_tree(top)
    req["tree"] = tree
    last = store(req["key"] + ".json")
    if tree and not req.get("force"):
        try:
            if json.load(open(last)).get("tree") == tree:
                return []
        except Exception:
            pass

    # Which server.
    server_cwd = None
    over = runtime_override(top) or {}
    base = req.get("url") or over.get("url")
    if base:
        if not is_loopback(base):
            return [status_line(out, "url_not_local", req)]
        base = origin(base)
    else:
        servers = discover(top)
        if not servers:
            return [status_line(out, "no_dev_server", req)]
        base, server_cwd = servers[0]["url"], servers[0]["cwd"]

    # Which routes.
    routes = list(req.get("routes") or over.get("routes") or [])
    if not routes:
        routes = routes_for(top, req.get("changed") or [], server_cwd)
        for r in manifest_routes(out, base):
            if r not in routes:
                routes.append(r)
        routes = routes or ["/"]
    routes = [r if r.startswith("/") else "/" + r for r in routes if isinstance(r, str)][:MAX_ROUTES]

    # Which Playwright.
    cands = pw_search if pw_search is not None else playwright_candidates([b for b in (server_cwd, top) if b])
    pw, tried = find_playwright(cands)
    if pw is None:
        return [status_line(out, "no_browser", req, hint=install_hint(tried))]

    rows_before = index_lines(out)
    n = 1 + max([r.get("n") or 0 for r in rows_before] or [0])
    stamp = time.strftime("%H%M%S")
    port = urllib.parse.urlsplit(base).port
    origins = sorted({"http://localhost:%s" % port, "http://127.0.0.1:%s" % port, "http://[::1]:%s" % port,
                      origin(base)})
    os.makedirs(out, exist_ok=True)
    items = []
    for r in routes:
        shots = [{"viewport": v, "file": os.path.join(out, "%03d-%s-%s-%s.png" % (n, stamp, v, slug(r)))}
                 for v in ("desktop", "phone")]
        items.append({"route": r, "url": base + r, "shots": shots})
    kill_after = int(os.environ.get("DEBRIEF_SCREENS_KILL_AFTER", KILL_AFTER))
    job = {"pkg": pw["pkg"], "origins": origins, "items": items, "webkit": bool(pw.get("webkit")),
           "max_height": MAX_HEIGHT, "deadline_ms": max(1000, (kill_after - 3) * 1000)}
    fd, job_path = tempfile.mkstemp(prefix="screen-job-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(job, f)
    timed_out = False
    try:
        env = dict(os.environ, PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1")
        proc = subprocess.Popen([node_bin(), MJS, "capture", job_path], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
                                start_new_session=True)
        try:
            proc.wait(timeout=kill_after)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # the browsers are in the same group
            except Exception:
                pass
            proc.wait()
        try:
            result = json.load(open(job_path + ".result"))
        except Exception:
            result = {"shots": [], "error": "no result"}
    finally:
        for p in (job_path, job_path + ".result"):
            try:
                os.remove(p)
            except OSError:
                pass

    err = result.get("error") or ""
    if err.startswith("no_browser"):
        return [status_line(out, "no_browser", req, hint=install_hint(tried), error=err[:300])]
    after = design_tree(top) if tree else None
    stale = bool(tree and after and after != tree)
    red = redactor()
    rows = []
    for s in result.get("shots", []):
        f = s.get("file")
        have = s.get("ok") and f and os.path.isfile(f)
        be = s.get("build_error")
        if be and isinstance(be, dict):
            # Overlay text is text: redacted, or dropped when no redactor loads.
            be = {"kind": be.get("kind"), "text": red(be.get("text") or "")[0] if red else None}
        rows.append({
            "n": n, "kind": "screen", "file": os.path.relpath(f, os.path.dirname(out)) if have else None,
            "route": s.get("route"), "url": s.get("url"), "viewport": s.get("viewport"),
            "engine": s.get("engine"), "http_status": s.get("http_status"), "build_error": be,
            "stale": stale, "tree": tree, "turn": req.get("turn"), "at": now_iso(),
            "status": "build_error" if be else ("ok" if have else "error"),
            **({"capped": True} if s.get("capped") else {}),
            **({"error": s["error"]} if s.get("error") else {}),
        })
    if timed_out or err:
        rows.append({"n": n, "kind": "screen", "file": None, "route": None, "url": base, "viewport": None,
                     "engine": None, "http_status": None, "build_error": None, "stale": stale, "tree": tree,
                     "turn": req.get("turn"), "at": now_iso(),
                     "status": "timeout" if timed_out else "error", "error": err[:300] or None})
    if result.get("counters"):
        for r in rows:
            r["blocked"] = result["counters"]
    append(out, rows)
    if tree and any(r["status"] in ("ok", "build_error") for r in rows):
        os.makedirs(os.path.dirname(last), exist_ok=True)
        with open(last, "w") as f:
            json.dump({"tree": tree, "at": now_iso()}, f)
    return rows


# ---------------------------------------------------------------- queue


def run_request(req, pw_search=None):
    """One capture per repo at a time. A request that finds the lock held waits
    in a one-slot queue; a newer request replaces it. The holder drains it."""
    os.makedirs(store(), exist_ok=True)
    pending = store(req["key"] + ".pending.json")
    tmp = "%s.%d.tmp" % (pending, os.getpid())
    with open(tmp, "w") as f:
        json.dump(req, f)
    os.replace(tmp, pending)
    written = []
    while True:
        fd = os.open(store(req["key"] + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return written  # the holder will run our request
            while True:
                try:
                    job = json.load(open(pending))
                    os.remove(pending)
                except Exception:
                    break
                try:
                    written += [r for r in capture(job, pw_search) if r]
                except Exception as e:
                    written += [r for r in [status_line(job["out"], "error", job, error=str(e)[:300])] if r]
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        # A request that arrived between our last read and the unlock.
        if not os.path.exists(pending):
            return written


def build_request(a):
    cwd = os.path.abspath(a.cwd)
    top = run_quiet(["git", "-C", cwd, "rev-parse", "--show-toplevel"], 5).strip() or cwd
    key = hashlib.sha1(os.path.realpath(top).encode()).hexdigest()[:12]
    return {"top": top, "key": key, "out": os.path.abspath(a.out), "url": a.url, "routes": a.route or [],
            "session": a.session, "turn": a.turn, "tree": a.tree, "changed": a.changed or [],
            "force": a.force}


def main():
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    ap = argparse.ArgumentParser(description="Screenshots from the dev server this repo runs.")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--cwd", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--url")
    r.add_argument("--route", action="append")
    r.add_argument("--session")
    r.add_argument("--turn", type=int)
    r.add_argument("--tree")
    r.add_argument("--changed", action="append")
    r.add_argument("--force", action="store_true", help="capture even when the design tree is unchanged")
    r.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if a.cmd != "run":
        ap.print_help(sys.stderr)
        sys.exit(2)
    if os.environ.get("DEBRIEF_SCREENS", "").strip() in ("", "0"):
        if not a.quiet:
            print("screens are off: set DEBRIEF_SCREENS=1", file=sys.stderr)
        return
    try:
        rows = run_request(build_request(a))
    except Exception as e:
        rows = []
        if not a.quiet:
            print("screen-capture: %s" % e, file=sys.stderr)
    statuses = sorted({r["status"] for r in rows})
    mark("ok" if statuses and set(statuses) <= {"ok"} else ("fail" if statuses else "ok"),
         "%d rows: %s" % (len(rows), ", ".join(statuses) or "nothing new"))
    if not a.quiet:
        for row in rows:
            print(json.dumps(row))


# ---------------------------------------------------------------- selftest


SERVER = r'''
import http.server, socketserver, sys, time
log, port = sys.argv[1], int(sys.argv[2])
other = sys.argv[3] if len(sys.argv) > 3 else "0"
PAGES = {
 "/": """<!doctype html><html><head><title>t</title></head><body><h1>Home</h1>
<img src="http://127.0.0.1:%s/remote.png">
<script>
fetch("/api/write", {method: "POST", body: "x"}).catch(() => {});
fetch("http://127.0.0.1:%s/cross").catch(() => {});
navigator.sendBeacon && navigator.sendBeacon("/beacon", "x");
try { new WebSocket("ws://" + location.host + "/ws"); } catch (e) {}
</script></body></html>""" % (other, other),
 "/settings": "<!doctype html><html><body><h1>Settings</h1></body></html>",
 "/vite": """<!doctype html><html><body><p>app</p><script>
customElements.define("vite-error-overlay", class extends HTMLElement { constructor() { super();
 this.attachShadow({mode: "open"}).innerHTML = "<div>[plugin:vite] Failed to resolve import</div>"; } });
document.body.appendChild(document.createElement("vite-error-overlay"));</script></body></html>""",
 "/next": """<!doctype html><html><body><p>app</p><script>
const p = document.createElement("nextjs-portal"); document.body.appendChild(p);
p.attachShadow({mode: "open"}).innerHTML = "<div data-nextjs-dialog>Build Error: Module not found</div>";
</script></body></html>""",
 "/indicator": """<!doctype html><html><body><p>fine</p><script>
const p = document.createElement("nextjs-portal"); document.body.appendChild(p);
p.attachShadow({mode: "open"}).innerHTML = "<div data-nextjs-toast>N</div>";</script></body></html>""",
}
class H(http.server.BaseHTTPRequestHandler):
    def _log(self):
        with open(log, "a") as f: f.write("%s %s\n" % (self.command, self.path))
    def _send(self, code, body, ctype="text/html"):
        b = body.encode(); self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers()
        if self.command != "HEAD": self.wfile.write(b)
    def do_GET(self):
        self._log()
        if self.path == "/hang": time.sleep(60)
        if self.path in PAGES: return self._send(200, PAGES[self.path])
        return self._send(404, "<!doctype html><p>nope</p>")
    do_HEAD = do_GET
    def do_POST(self): self._log(); self._send(200, "ok", "text/plain")
    def do_OPTIONS(self): self._log(); self._send(204, "")
    def log_message(self, *a): pass
class S(socketserver.ThreadingMixIn, http.server.HTTPServer): daemon_threads = True
S(("127.0.0.1", port), H).serve_forever()
'''


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def selftest():
    fails = []

    def check(name, ok, detail=""):
        print(("ok " if ok else "FAIL ") + name + (": %s" % (detail,) if detail and not ok else ""))
        if not ok:
            fails.append(name)

    td = tempfile.mkdtemp(prefix="screens-selftest-")
    procs = []
    try:
        _selftest(td, check, procs)
    finally:
        for p in procs:
            try:
                p.kill()
                p.wait(timeout=5)
            except Exception:
                pass
        shutil.rmtree(td, ignore_errors=True)
    print("ALL PASS" if not fails else "%d FAILED" % len(fails))
    return 1 if fails else 0


def _selftest(td, check, procs):
    os.environ["DEBRIEF_DIR"] = os.path.join(td, "debrief")

    # Route mapping.
    top = os.path.join(td, "repo")
    os.makedirs(os.path.join(top, "apps", "web"))
    os.makedirs(os.path.join(top, "lib"))
    json.dump({"dependencies": {"next": "15"}}, open(os.path.join(top, "package.json"), "w"))
    open(os.path.join(top, "apps", "web", "next.config.mjs"), "w").write("export default {}\n")
    cases = [
        ("app/page.tsx", ["/"]),
        ("app/settings/page.tsx", ["/settings"]),
        ("app/(marketing)/about/page.tsx", ["/about"]),
        ("app/blog/[slug]/page.tsx", []),
        ("app/@modal/(.)photo/page.tsx", []),
        ("app/_private/page.tsx", []),
        ("app/me/layout.tsx", ["/me"]),
        ("app/me/Button.tsx", []),
        ("src/app/friends/page.tsx", ["/friends"]),
        ("pages/index.tsx", ["/"]),
        ("pages/docs/intro.mdx", ["/docs/intro"]),
        ("pages/blog/[id].tsx", []),
        ("pages/api/hello.ts", []),
        ("pages/_app.tsx", ["/"]),
        ("lib/pages/foo.tsx", []),
        ("components/Card.tsx", []),
        ("apps/web/app/inbox/page.tsx", ["/inbox"]),
        ("styles/globals.css", []),
    ]
    for rel, want in cases:
        got = routes_for(top, [rel])
        check("route: %s -> %s" % (rel, want or "nothing (falls back to /)"), got == want, got)
    check("route: another app in the monorepo is skipped when the server runs elsewhere",
          routes_for(top, ["apps/web/app/inbox/page.tsx"], server_cwd=top) == []
          and routes_for(top, ["app/settings/page.tsx"], server_cwd=top) == ["/settings"]
          and routes_for(top, ["apps/web/app/inbox/page.tsx"], server_cwd=os.path.join(top, "apps", "web")) == ["/inbox"]
          and routes_for(top, ["app/settings/page.tsx"], server_cwd=os.path.join(top, "apps", "web")) == [])
    check("route: at most three, deduplicated", len((routes_for(top, ["app/a/page.tsx", "app/b/page.tsx",
          "app/c/page.tsx", "app/d/page.tsx", "app/a/layout.tsx"]) + ["/"])[:MAX_ROUTES]) == 3)
    check("loopback only", is_loopback("http://localhost:3000/x") and is_loopback("http://127.0.0.1:1")
          and is_loopback("http://[::1]:5173/") and not is_loopback("http://example.com/")
          and not is_loopback("http://10.0.0.2:3000/"))

    # Servers: one started inside the repo, one started elsewhere.
    subprocess.run(["git", "-C", top, "init", "-q"], capture_output=True)
    elsewhere = os.path.join(td, "elsewhere")
    os.makedirs(elsewhere)
    script = os.path.join(td, "srv.py")
    open(script, "w").write(SERVER)
    pin, pout = _free_port(), _free_port()
    log_in, log_out = os.path.join(td, "in.log"), os.path.join(td, "out.log")
    procs.append(subprocess.Popen([sys.executable, script, log_in, str(pin), str(pout)], cwd=top,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    procs.append(subprocess.Popen([sys.executable, script, log_out, str(pout)], cwd=elsewhere,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    end = time.time() + 10
    while time.time() < end and not (answers_html("http://127.0.0.1:%d" % pin) and answers_html("http://127.0.0.1:%d" % pout)):
        time.sleep(0.1)
    for lf in (log_in, log_out):
        open(lf, "w").close()
    found = discover(top)
    ports = [s["port"] for s in found]
    check("discovery: the server this repo started is found", pin in ports, found)
    check("discovery: a server started elsewhere is ignored", pout not in ports, found)

    # No server: a repo nobody serves.
    lonely = os.path.join(td, "lonely")
    os.makedirs(lonely)
    subprocess.run(["git", "-C", lonely, "init", "-q"], capture_output=True)
    out0 = os.path.join(td, "m0", "screens")
    rows = run_request({"top": lonely, "key": "lonely", "out": out0, "changed": [], "tree": "t0", "turn": 1})
    check("no server: status no_dev_server", rows and rows[0]["status"] == "no_dev_server", rows)
    rows = run_request({"top": lonely, "key": "lonely", "out": out0, "changed": [], "tree": "t1", "turn": 2})
    check("no server: the same status is not repeated", rows == [] and len(index_lines(out0)) == 1, rows)
    rows = run_request({"top": top, "key": "x", "out": out0, "url": "http://example.com/", "tree": "t2"})
    check("a non-loopback url is refused", rows and rows[0]["status"] == "url_not_local", rows)

    # No browser: no Playwright anywhere it looks.
    out1 = os.path.join(td, "m1", "screens")
    rows = run_request({"top": top, "key": "nob", "out": out1, "changed": [], "tree": "t3"}, pw_search=[])
    check("no Playwright: status no_browser with one command", rows and rows[0]["status"] == "no_browser"
          and rows[0].get("hint"), rows)

    # The queue: a held lock leaves one pending request, the newest.
    os.makedirs(store(), exist_ok=True)
    fd = os.open(store("q.lock"), os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    run_request({"top": lonely, "key": "q", "out": out0, "tree": "old"})
    run_request({"top": lonely, "key": "q", "out": out0, "tree": "new"})
    pend = json.load(open(store("q.pending.json")))
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    check("queue: a newer request replaces a queued one", pend["tree"] == "new", pend)

    pw, tried = find_playwright(playwright_candidates([top]))
    if pw is None:
        print("SKIP browser tests: no Playwright with a cached Chromium on this machine")
        return
    print("using Playwright %s at %s (webkit: %s)" % (pw.get("version"), pw["pkg"], pw.get("webkit")))

    # A real capture with routes from the changed files.
    os.makedirs(os.path.join(top, "app", "settings"), exist_ok=True)
    out2 = os.path.join(td, "m2", "screens")
    t0 = time.time()
    real = design_tree(top)
    check("the design tree hash is computed", bool(real) and len(real) >= 40, real)
    rows = run_request({"top": top, "key": "cap", "out": out2, "tree": real, "turn": 4,
                        "changed": ["app/page.tsx", "app/settings/page.tsx"]}, pw_search=[pw["pkg"]])
    took = time.time() - t0
    ok = [r for r in rows if r["status"] == "ok"]
    check("capture: desktop and phone for each route", len(ok) == 4
          and {r["route"] for r in ok} == {"/", "/settings"}
          and all(r["viewport"] == "1440x900" for r in ok if "-desktop-" in r["file"])
          and all(r["viewport"] != "1440x900" for r in ok if "-phone-" in r["file"]), rows)
    check("capture: files are PNGs named n-time-viewport-route",
          all(re.match(r"screens/001-\d{6}-(desktop|phone)-(root|settings)\.png$", r["file"]) for r in ok)
          and all(open(os.path.join(os.path.dirname(out2), r["file"]), "rb").read(8) == b"\x89PNG\r\n\x1a\n" for r in ok),
          [r["file"] for r in ok])
    phone = [r for r in ok if "-phone-" in r["file"]]
    check("capture: the phone engine is recorded", phone and phone[0]["engine"] in ("webkit", "chromium")
          and (phone[0]["engine"] == "webkit") == bool(pw.get("webkit")), phone[:1])
    check("capture: fields recorded", ok and all(r["tree"] == real and r["turn"] == 4 and r["http_status"] == 200
                                                  and r["stale"] is False for r in ok), ok[:1])
    reqs = open(log_in).read().split("\n")
    check("read-only: positive control, the page itself was served", "GET /" in reqs, reqs)
    check("read-only: no POST reached the server (fetch or beacon)", not [x for x in reqs if x.startswith("POST")], reqs)
    check("read-only: the cross-origin GET and image never left the page", open(log_out).read() == "",
          open(log_out).read())
    check("read-only: the WebSocket never reached the server", not [x for x in reqs if "/ws" in x], reqs)
    check("read-only: blocked requests are counted", ok and ok[0]["blocked"]["blocked_method"] >= 1
          and ok[0]["blocked"]["blocked_origin"] >= 1, ok[:1] and ok[0].get("blocked"))
    print("   (capture of 2 routes x 2 viewports took %.1fs)" % took)

    # Same tree again: skipped.
    rows = run_request({"top": top, "key": "cap", "out": out2, "tree": real, "turn": 5,
                        "changed": ["app/page.tsx"]}, pw_search=[pw["pkg"]])
    check("an unchanged design tree is not captured again", rows == [], rows)

    # Build-error overlays.
    out3 = os.path.join(td, "m3", "screens")
    rows = run_request({"top": top, "key": "ovl", "out": out3, "tree": "tree-b", "turn": 6,
                        "routes": ["/vite", "/next", "/indicator"]}, pw_search=[pw["pkg"]])
    check("stale: a tree that changed during the capture is marked", rows and all(r["stale"] is True for r in rows),
          [r["stale"] for r in rows])
    by = {}
    for r in rows:
        by.setdefault(r["route"], []).append(r)
    check("overlay: Vite's error overlay is build_error", by.get("/vite") and all(
        r["status"] == "build_error" and r["build_error"]["kind"] == "vite" for r in by["/vite"]), by.get("/vite"))
    check("overlay: Next.js's error dialog is build_error", by.get("/next") and all(
        r["status"] == "build_error" and r["build_error"]["kind"] == "next" for r in by["/next"]), by.get("/next"))
    check("overlay: Next.js's dev indicator alone is not an error", by.get("/indicator") and all(
        r["status"] == "ok" for r in by["/indicator"]), by.get("/indicator"))

    # Hard kill: a page that never finishes loading.
    out4 = os.path.join(td, "m4", "screens")
    os.environ["DEBRIEF_SCREENS_KILL_AFTER"] = "6"
    t0 = time.time()
    rows = run_request({"top": top, "key": "hang", "out": out4, "tree": "tree-c", "routes": ["/hang"]},
                       pw_search=[pw["pkg"]])
    took = time.time() - t0
    os.environ.pop("DEBRIEF_SCREENS_KILL_AFTER")
    check("hard kill: a hung capture is stopped and recorded", took < 12 and rows
          and rows[-1]["status"] in ("timeout", "error") and not [r for r in rows if r["status"] == "ok"],
          (took, rows))

    # The runtime override wins over discovery.
    os.makedirs(os.path.join(top, ".debrief"))
    json.dump({"url": "http://127.0.0.1:%d" % pin, "routes": ["/settings"]},
              open(os.path.join(top, ".debrief", "runtime.json"), "w"))
    out5 = os.path.join(td, "m5", "screens")
    rows = run_request({"top": top, "key": "ovr", "out": out5, "tree": "tree-d", "changed": ["app/page.tsx"]},
                       pw_search=[pw["pkg"]])
    check("override: runtime.json url and routes win", rows and {r["route"] for r in rows} == {"/settings"}
          and all(r["url"].startswith("http://127.0.0.1:%d" % pin) for r in rows), rows)


if __name__ == "__main__":
    main()
