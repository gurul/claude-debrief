#!/usr/bin/env python3
"""extract-material — keep a session's originals where the next agent can open them.

A daily says "the header got calmer". The screenshot that showed it, the
version of the component that was rejected, the reference that was pasted in
and the link that was pointed at are gone with the transcript, or sit in it as
base64 nobody can look at. This script lifts them out into plain files:

    sessions/material/<sid8>/
      manifest.json            what each file is, and where in the session it came from
      images/<sha12>.<ext>     every image the session saw (pasted, screenshot, Read)
      code/<NNN>-<name>        each version a Write produced of a design file
      code/<NNN>-<name>.patch  each Edit to a design file, as a diff
      code/final/<name>        the file on disk when the session ended (live only)
      code/<repo>.diff         uncommitted changes to those files at the end (live only)
      artifacts/<NNN>-<sha8>.html   pages the session published
      references/<NNN>-<sha8>.md    long text the human pasted in

Material is evidence, not memory. It is never indexed by debrief-search.py:
it is reached from a curated note through `debrief-search.py source <session>`.

Rules:
  * The transcript format is internal and unstable. Nothing here depends on
    a field being present: every block is found by a tolerant walk, and a
    line that does not parse is skipped and counted.
  * Text goes through redact-transcript.py before it lands. No redactor means
    no text is written (fail closed). Images cannot be scrubbed: read
    docs/material.md before turning this on.
  * Re-running on a longer copy of the same session adds to the folder and
    never rewrites a file. Files are named by content hash or by order.

Usage:
    extract-material.py <transcript.jsonl[.gz]> <material-root> --session <id> [--snapshot]
    extract-material.py --backfill <debrief-dir>
    extract-material.py --selftest
"""

import base64
import binascii
import difflib
import fnmatch
import gzip
import hashlib
import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import zlib

MAX_IMAGE = 20 * 1024 * 1024
MAX_TEXT = 512 * 1024
MIN_REFERENCE = 2000
MAX_LINKS = 300
KINDS = ("images", "code", "artifacts", "references", "links")

DESIGN_EXT = {
    ".tsx", ".jsx", ".vue", ".svelte", ".astro", ".html", ".htm", ".css",
    ".scss", ".sass", ".less", ".svg", ".swift", ".dart", ".mdx",
}
DESIGN_NAMES = [
    "design*.md", "DESIGN*.md", "*tokens*.json", "*tokens*.ts", "tailwind.config.*",
    "theme.*", "*.theme.*", "*.storyboard", "*.xib",
]
IMAGE_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
# Text the harness injects into a user turn. It is not something the human pasted.
INJECTED = (
    "<system-reminder>", "Base directory for this skill", "<local-command", "<command-",
    "<task-notification", "This session is being continued", "Caveat:", "[Request interrupted",
)
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
URL = re.compile(r"https?://[^\s\"'<>)\]\\]+")


def design_exts():
    env = os.environ.get("DEBRIEF_MATERIAL_EXT", "").strip()
    if not env:
        return DESIGN_EXT
    return {e if e.startswith(".") else "." + e for e in (x.strip().lower() for x in env.split(",")) if e}


def is_design(path):
    base = os.path.basename(path)
    if os.path.splitext(base)[1].lower() in design_exts():
        return True
    return any(fnmatch.fnmatch(base, pat) for pat in DESIGN_NAMES)


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


def image_ok(data, media):
    """True when the bytes are a whole image of the stated kind.

    A raw archive was redacted as text, and a pattern can land inside base64.
    A damaged image is worse than a missing one, because it still opens.
    """
    if media == "image/png":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            return False
        i = 8
        while i + 12 <= len(data):
            (n,) = struct.unpack(">I", data[i : i + 4])
            kind = data[i + 4 : i + 8]
            end = i + 12 + n
            if end > len(data):
                return False
            if zlib.crc32(data[i + 4 : end - 4]) & 0xFFFFFFFF != struct.unpack(">I", data[end - 4 : end])[0]:
                return False
            if kind == b"IEND":
                return True
            i = end
        return False
    if media == "image/jpeg":
        return data.startswith(b"\xff\xd8") and data.rstrip(b"\x00")[-2:] == b"\xff\xd9"
    if media == "image/gif":
        return data[:6] in (b"GIF87a", b"GIF89a") and data.rstrip(b"\x00").endswith(b";")
    if media == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP" and struct.unpack("<I", data[4:8])[0] + 8 <= len(data) + 1
    return False


def open_text(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def slug(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80] or "file"


class Extractor:
    def __init__(self, out_dir, redact):
        self.out = out_dir
        self.redact = redact
        self.manifest = {k: [] for k in KINDS}
        mp = os.path.join(out_dir, "manifest.json")
        if os.path.exists(mp):
            try:
                old = json.load(open(mp))
                for k in KINDS:
                    self.manifest[k] = list(old.get(k, []))
                for k in ("session_id", "cwd", "git", "sources"):
                    if k in old:
                        self.manifest[k] = old[k]
            except Exception:
                pass
        self.seen = {k: {e.get("key") for e in self.manifest[k]} for k in KINDS}
        self.tools = {}  # tool_use id -> (name, input)
        self.added = {k: 0 for k in KINDS}
        self.skipped = {"bad_lines": 0, "damaged_images": 0, "oversize": 0, "text_no_redactor": 0}
        self.touched = []  # design paths the session wrote or edited, in order
        self.cwd = None

    # ---- writing -------------------------------------------------------
    def _put(self, sub, name, data):
        d = os.path.join(self.out, sub)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        if not os.path.exists(p):
            with open(p, "wb") as f:
                f.write(data)
        return os.path.join(sub, name)

    def _text(self, text):
        """Redacted bytes, or None when text may not be written."""
        if self.redact is None:
            self.skipped["text_no_redactor"] += 1
            return None
        text, _ = self.redact(text)
        data = text.encode("utf-8", errors="replace")
        if len(data) > MAX_TEXT:
            self.skipped["oversize"] += 1
            return None
        return data

    def _add(self, kind, key, entry):
        if key in self.seen[kind]:
            return False
        self.seen[kind].add(key)
        entry["key"] = key
        self.manifest[kind].append(entry)
        self.added[kind] += 1
        return True

    def _n(self, kind):
        return "%03d" % (len(self.manifest[kind]) + 1)

    # ---- blocks --------------------------------------------------------
    def image(self, block, line, origin):
        src = block.get("source") if isinstance(block.get("source"), dict) else {}
        data, media = src.get("data"), src.get("media_type", "")
        if not isinstance(data, str) or media not in IMAGE_EXT:
            return
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            self.skipped["damaged_images"] += 1
            return
        if len(raw) > MAX_IMAGE:
            self.skipped["oversize"] += 1
            return
        if not image_ok(raw, media):
            self.skipped["damaged_images"] += 1
            return
        sha = hashlib.sha256(raw).hexdigest()
        if sha in self.seen["images"]:
            return
        rel = self._put("images", sha[:12] + IMAGE_EXT[media], raw)
        self._add("images", sha, dict(file=rel, media_type=media, bytes=len(raw), line=line, **origin))

    def tool_use(self, block, line, ts):
        name, inp = block.get("name") or "", block.get("input")
        if not isinstance(inp, dict):
            return
        if block.get("id"):
            self.tools[block["id"]] = (name, inp)
        for k in ("url", "URL"):
            if isinstance(inp.get(k), str):
                self.link(inp[k], name, line)
        path = inp.get("file_path") or inp.get("path") or ""
        content = inp.get("content")
        if name == "Write" and isinstance(path, str) and isinstance(content, str) and is_design(path):
            self.code(path, content, "write", line, ts)
        elif name == "Edit" and isinstance(path, str) and is_design(path):
            old, new = inp.get("old_string"), inp.get("new_string")
            if isinstance(old, str) and isinstance(new, str):
                diff = "".join(difflib.unified_diff(
                    old.splitlines(True), new.splitlines(True), "a/" + path, "b/" + path, n=2))
                if diff:
                    self.code(path, diff, "edit", line, ts)
        elif "artifact" in name.lower() and isinstance(content, str):
            if content.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
                self.artifact(content, name, inp, line, ts)

    def code(self, path, text, kind, line, ts):
        if path not in self.touched:
            self.touched.append(path)
        sha = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        key = "%s:%s:%s" % (kind, path, sha)
        if key in self.seen["code"]:
            return
        data = self._text(text)
        if data is None:
            return
        name = "%s-%s%s" % (self._n("code"), slug(os.path.basename(path)), ".patch" if kind == "edit" else "")
        rel = self._put("code", name, data)
        self._add("code", key, dict(file=rel, path=path, kind=kind, bytes=len(data), line=line, at=ts))

    def artifact(self, html, tool, inp, line, ts):
        sha = hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest()
        if sha in self.seen["artifacts"]:
            return
        data = self._text(html)
        if data is None:
            return
        rel = self._put("artifacts", "%s-%s.html" % (self._n("artifacts"), sha[:8]), data)
        title = inp.get("title") if isinstance(inp.get("title"), str) else ""
        self._add("artifacts", sha, dict(file=rel, tool=tool, title=title, bytes=len(data), line=line, at=ts))

    def user_text(self, text, line, ts):
        text = REMINDER.sub("", text)
        for u in URL.findall(text):
            self.link(u.rstrip(".,;:`*"), "user", line)
        body = text.strip()
        if len(body) < MIN_REFERENCE or body.startswith(INJECTED):
            return
        sha = hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()
        if sha in self.seen["references"]:
            return
        data = self._text(body)
        if data is None:
            return
        rel = self._put("references", "%s-%s.md" % (self._n("references"), sha[:8]), data)
        first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")[:100]
        self._add("references", sha, dict(file=rel, bytes=len(data), line=line, at=ts, starts=first))

    def link(self, url, origin, line):
        if len(self.manifest["links"]) >= MAX_LINKS or len(url) > 500:
            return
        if self.redact is not None:
            url, _ = self.redact(url)
        self._add("links", url, {"url": url, "from": origin, "line": line})

    # ---- walk ----------------------------------------------------------
    def feed(self, obj, line):
        if not isinstance(obj, dict):
            return
        if self.cwd is None and isinstance(obj.get("cwd"), str):
            self.cwd = obj["cwd"]
        ts = obj.get("timestamp") if isinstance(obj.get("timestamp"), str) else ""
        # A message the human sent while the agent was working is queued, not
        # a user turn. Much of their steering arrives this way.
        if obj.get("type") == "queue-operation" and obj.get("operation") == "enqueue" \
                and isinstance(obj.get("content"), str):
            self.user_text(obj["content"], line, ts)
            return
        msg = obj.get("message")
        if not isinstance(msg, dict):
            return
        role = msg.get("role") or obj.get("type")
        # Only a real user turn carries the human's words. Request blobs repeat
        # the whole context, with skill text and reminders the harness put in.
        human = (obj.get("type") == "user" and role == "user"
                 and not obj.get("isMeta") and not obj.get("isSidechain"))
        content = msg.get("content")
        if isinstance(content, str):
            if human:
                self.user_text(content, line, ts)
            return
        if not isinstance(content, list):
            return
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text" and human and isinstance(b.get("text"), str):
                self.user_text(b["text"], line, ts)
            elif t == "image":
                self.image(b, line, {"from": "user"} if role == "user" else {"from": str(role)})
            elif t == "tool_use":
                self.tool_use(b, line, ts)
            elif t == "tool_result" and isinstance(b.get("content"), list):
                name, inp = self.tools.get(b.get("tool_use_id"), ("", {}))
                origin = {"from": name or "tool"}
                for k in ("file_path", "path", "url"):
                    if isinstance(inp.get(k), str):
                        origin["source"] = inp[k][:300]
                        break
                for x in b["content"]:
                    if isinstance(x, dict) and x.get("type") == "image":
                        self.image(x, line, origin)

    def run(self, transcript):
        with open_text(transcript) as fh:
            for n, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    self.feed(json.loads(raw), n)
                except Exception:
                    self.skipped["bad_lines"] += 1

    # ---- the state on disk at session end (live sessions only) ----------
    def snapshot(self):
        repos = {}
        for path in self.touched:
            if not os.path.isfile(path):
                continue
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            sha = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
            key = "final:%s:%s" % (path, sha)
            if key not in self.seen["code"]:
                data = self._text(text)
                if data is not None:
                    rel = self._put(os.path.join("code", "final"), "%s-%s" % (sha[:8], slug(os.path.basename(path))), data)
                    self._add("code", key, dict(file=rel, path=path, kind="final", bytes=len(data)))
            top = git(os.path.dirname(path), "rev-parse", "--show-toplevel")
            if top:
                repos.setdefault(top, []).append(path)
        state = []
        for top, paths in repos.items():
            head = git(top, "rev-parse", "HEAD")
            branch = git(top, "rev-parse", "--abbrev-ref", "HEAD")
            state.append({"repo": top, "branch": branch, "head": head})
            diff = git(top, "diff", "HEAD", "--", *paths, strip=False) if head else ""
            if diff:
                sha = hashlib.sha256(diff.encode("utf-8", errors="replace")).hexdigest()
                key = "diff:%s:%s" % (top, sha)
                if key not in self.seen["code"]:
                    data = self._text(diff)
                    if data is not None:
                        rel = self._put("code", "%s-%s.diff" % (sha[:8], slug(os.path.basename(top))), data)
                        self._add("code", key, dict(file=rel, path=top, kind="diff", bytes=len(data), head=head))
        if state:
            self.manifest["git"] = state

    def save(self, session_id, transcript):
        if not any(self.manifest[k] for k in KINDS):
            return False
        os.makedirs(self.out, exist_ok=True)
        self.manifest["session_id"] = session_id
        if self.cwd:
            self.manifest["cwd"] = self.cwd
        self.manifest.setdefault("sources", [])
        if transcript not in self.manifest["sources"]:
            self.manifest["sources"].append(transcript)
        self.manifest["note"] = "Unverified originals. Evidence, not memory. See docs/material.md."
        tmp = os.path.join(self.out, "manifest.json.tmp")
        with open(tmp, "w") as f:
            json.dump(self.manifest, f, indent=1)
        os.replace(tmp, os.path.join(self.out, "manifest.json"))
        return True


def git(cwd, *args, strip=True):
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=20)
    except Exception:
        return ""
    if r.returncode != 0:
        return ""
    return r.stdout.strip() if strip else r.stdout


def extract(transcript, root, session_id, snapshot=False, redact="load"):
    if redact == "load":
        redact = load_redactor(os.path.dirname(os.path.abspath(__file__)))
    ex = Extractor(os.path.join(root, session_id[:8]), redact)
    ex.run(transcript)
    if snapshot:
        ex.snapshot()
    ex.save(session_id, transcript)
    return ex


def summary(ex):
    parts = ["%d %s" % (ex.added[k], k) for k in KINDS if ex.added[k]]
    skips = ["%d %s" % (v, k) for k, v in ex.skipped.items() if v]
    return (", ".join(parts) or "nothing new") + (" [skipped: " + ", ".join(skips) + "]" if skips else "")


def backfill(debrief):
    """Every archived session, oldest first. A live transcript beats its redacted copy."""
    root = os.path.join(debrief, "sessions", "material")
    live = {}
    q = os.path.join(debrief, "sessions", "queue.jsonl")
    if os.path.exists(q):
        for ln in open(q):
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if os.path.isfile(d.get("transcript_path", "")):
                live[d.get("session_id", "")[:8]] = (d["transcript_path"], d.get("session_id", ""))
    done = set()
    total = 0
    raw = os.path.join(debrief, "sessions", "raw")
    for day in sorted(os.listdir(raw)) if os.path.isdir(raw) else []:
        for name in sorted(os.listdir(os.path.join(raw, day))):
            m = re.match(r"\d{4}-([0-9a-f]{8})\.jsonl\.gz$", name)
            if not m:
                continue
            sid = m.group(1)
            path, full = live.get(sid, (os.path.join(raw, day, name), sid))
            if (sid, path) in done:
                continue
            done.add((sid, path))
            try:
                ex = extract(path, root, full)
            except Exception as e:
                print("%s: FAILED %s" % (sid, e))
                continue
            n = sum(ex.added.values())
            total += n
            if n:
                print("%s (%s): %s" % (sid, day, summary(ex)))
    print("backfill: %d new items across %d sessions" % (total, len({s for s, _ in done})))


# ---------------------------------------------------------------- selftest


def _png(color=b"\xff\x00\x00"):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00" + color)) + chunk(b"IEND", b"")


def selftest():
    import tempfile

    fails = []

    def check(name, ok, detail=""):
        print(("ok " if ok else "FAIL ") + name + (": %s" % (detail,) if detail and not ok else ""))
        if not ok:
            fails.append(name)

    here = os.path.dirname(os.path.abspath(__file__))
    red, blue = _png(), _png(b"\x00\x00\xff")
    b64 = lambda b: base64.b64encode(b).decode()
    img = lambda b: {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64(b)}}
    damaged = bytearray(red)
    damaged[40] ^= 0xFF
    essay = "A reference the human pasted.\n" + ("It says something about calm interfaces. " * 80)
    secret = "sk-ant-api03-" + "A" * 40
    lines = [
        {"type": "user", "cwd": "/w", "timestamp": "t1", "message": {"role": "user", "content": [
            {"type": "text", "text": "make it calmer, see https://www.figma.com/design/abc/File?node-id=1-2."},
            img(red)]}},
        {"type": "assistant", "timestamp": "t2", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "u1", "name": "Read", "input": {"file_path": "/tmp/shot.png"}},
            {"type": "tool_use", "id": "u2", "name": "Write", "input": {
                "file_path": "/w/src/Header.tsx", "content": "export const Header = () => <h1>loud</h1>\nconst k='%s'\n" % secret}},
            {"type": "tool_use", "id": "u3", "name": "Write", "input": {"file_path": "/w/server/db.py", "content": "x=1\n"}},
            {"type": "tool_use", "id": "u4", "name": "Edit", "input": {
                "file_path": "/w/src/Header.tsx", "old_string": "<h1>loud</h1>", "new_string": "<h1>calm</h1>"}},
            {"type": "tool_use", "id": "u5", "name": "mcp__claude-artifacts__publish_version", "input": {
                "title": "Header study", "content": "<!DOCTYPE html><html><body>study</body></html>"}},
            {"type": "tool_use", "id": "u6", "name": "WebFetch", "input": {"url": "https://example.com/ref"}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "u1", "content": [img(blue), img(bytes(damaged))]}]}},
        {"type": "api-request-blob", "message": {"role": "user", "content": [img(red)]}},
        {"type": "user", "message": {"role": "user", "content": essay}},
        {"type": "queue-operation", "operation": "enqueue", "timestamp": "t3",
         "content": "also look at https://midturn.example/ref while you work"},
        {"type": "queue-operation", "operation": "remove", "content": "see https://removed.example/x"},
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": "Base directory for this skill " + "x" * 3000}},
        {"type": "user", "message": {"role": "user", "content":
            "<system-reminder>see https://harness.example/x " + "y" * 3000 + "</system-reminder>ok"}},
        {"type": "api-request-blob", "message": {"role": "user", "content": "# A skill reference " + "z" * 3000}},
    ]
    with tempfile.TemporaryDirectory() as td:
        t = os.path.join(td, "t.jsonl")
        with open(t, "w") as f:
            for d in lines:
                f.write(json.dumps(d) + "\n")
            f.write("{not json\n")
        root = os.path.join(td, "material")
        ex = extract(t, root, "abcdef0123456789")
        out = os.path.join(root, "abcdef01")
        m = json.load(open(os.path.join(out, "manifest.json")))
        rd = lambda rel: open(os.path.join(out, rel), "rb").read()

        check("images: two distinct images, the repeat stored once", len(m["images"]) == 2, m["images"])
        check("images: bytes on disk equal the original", {rd(e["file"]) for e in m["images"]} == {red, blue})
        check("images: a damaged image is skipped and counted", ex.skipped["damaged_images"] == 1, ex.skipped)
        by = {e["from"]: e for e in m["images"]}
        check("images: a pasted image is marked as the human's", "user" in by)
        check("images: a tool image names its tool and source", by.get("Read", {}).get("source") == "/tmp/shot.png", by)
        code = {e["kind"]: e for e in m["code"]}
        check("code: a design file's Write is kept", "write" in code and b"loud" in rd(code["write"]["file"]))
        check("code: an Edit is kept as a diff", "edit" in code and b"+<h1>calm</h1>" in rd(code["edit"]["file"]))
        check("code: a file outside the design set is not kept", all("db.py" not in e["path"] for e in m["code"]))
        check("code: a secret in code is redacted", secret.encode() not in rd(code["write"]["file"])
              and b"[REDACTED:" in rd(code["write"]["file"]))
        check("artifacts: a published page is kept with its title",
              len(m["artifacts"]) == 1 and m["artifacts"][0]["title"] == "Header study"
              and b"study" in rd(m["artifacts"][0]["file"]))
        check("references: long pasted text is kept", len(m["references"]) == 1
              and rd(m["references"][0]["file"]).startswith(b"A reference the human pasted."))
        check("references: injected harness text is not a reference", len(m["references"]) == 1)
        urls = {e["url"]: e["from"] for e in m["links"]}
        check("links: a link the human gave, without the sentence's full stop",
              urls.get("https://www.figma.com/design/abc/File?node-id=1-2") == "user", urls)
        check("links: a link inside a harness reminder is not the human's", "https://harness.example/x" not in urls, urls)
        check("links: a link from a message sent mid-turn is the human's, once",
              urls.get("https://midturn.example/ref") == "user" and "https://removed.example/x" not in urls, urls)
        check("links: a link a tool opened", urls.get("https://example.com/ref") == "WebFetch", urls)
        check("a line that is not JSON is skipped and counted", ex.skipped["bad_lines"] == 1)

        before = sorted(os.listdir(os.path.join(out, "code")))
        with open(t, "a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "u9", "name": "Write", "input": {
                    "file_path": "/w/src/Header.tsx", "content": "export const Header = () => <h1>calmer</h1>\n"}}]}}) + "\n")
        ex2 = extract(t, root, "abcdef0123456789")
        after = sorted(os.listdir(os.path.join(out, "code")))
        check("resume: a longer copy adds only what is new", sum(ex2.added.values()) == 1 and len(after) == len(before) + 1,
              (ex2.added, before, after))
        check("resume: earlier files keep their names", set(before) <= set(after))

        # Fail closed: without a redactor no text lands, images still do.
        root2 = os.path.join(td, "m2")
        ex3 = extract(t, root2, "abcdef0123456789", redact=None)
        m3 = json.load(open(os.path.join(root2, "abcdef01", "manifest.json")))
        check("fail closed: no redactor, no code, artifact or reference",
              not m3["code"] and not m3["artifacts"] and not m3["references"] and ex3.skipped["text_no_redactor"] > 0)
        check("fail closed: images are still kept", len(m3["images"]) == 2)

        # Snapshot: the file on disk and the uncommitted diff, in a real repo.
        repo = os.path.join(td, "repo")
        os.makedirs(os.path.join(repo, "src"))
        f1 = os.path.join(repo, "src", "Card.tsx")
        open(f1, "w").write("export const Card = () => <div>v1</div>\n")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for a in (["init", "-q"], ["add", "."], ["commit", "-qm", "v1"]):
            subprocess.run(["git", "-C", repo, *a], env=env, capture_output=True)
        open(f1, "w").write("export const Card = () => <div>v2 never committed</div>\n")
        t2 = os.path.join(td, "t2.jsonl")
        open(t2, "w").write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "e1", "name": "Edit", "input": {
                "file_path": f1, "old_string": "v1", "new_string": "v2 never committed"}}]}}) + "\n")
        extract(t2, root, "11112222aaaa", snapshot=True)
        m4 = json.load(open(os.path.join(root, "11112222", "manifest.json")))
        kinds = {e["kind"]: e for e in m4["code"]}
        o4 = os.path.join(root, "11112222")
        check("snapshot: the file on disk at the end is kept",
              "final" in kinds and b"v2 never committed" in open(os.path.join(o4, kinds["final"]["file"]), "rb").read())
        check("snapshot: the uncommitted diff is kept",
              "diff" in kinds and b"+export const Card = () => <div>v2 never committed</div>"
              in open(os.path.join(o4, kinds["diff"]["file"]), "rb").read())
        check("snapshot: the repo, branch and head are recorded",
              bool(m4.get("git")) and len(m4["git"][0]["head"]) == 40)

        # A session with nothing to keep leaves no folder behind.
        t3 = os.path.join(td, "t3.jsonl")
        open(t3, "w").write(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")
        extract(t3, root, "99990000ffff")
        check("an empty session leaves no folder", not os.path.exists(os.path.join(root, "99990000")))

        check("image check: a truncated PNG is refused", not image_ok(red[:-6], "image/png"))
        check("design set: a component is in, a server file is out, a tokens file is in",
              is_design("a/B.tsx") and not is_design("a/b.py") and is_design("x/design-tokens.json") and is_design("DESIGN.md"))
        check("the sibling redactor loads", load_redactor(here) is not None)

    print("ALL PASS" if not fails else "%d FAILED" % len(fails))
    sys.exit(1 if fails else 0)


def main():
    a = sys.argv[1:]
    if a == ["--selftest"]:
        selftest()
    if len(a) == 2 and a[0] == "--backfill":
        backfill(a[1])
        return
    if len(a) >= 4 and a[2] == "--session":
        ex = extract(a[0], a[1], a[3], snapshot="--snapshot" in a[4:])
        print(summary(ex))
        return
    sys.exit(__doc__.strip().split("Usage:")[1].strip())


if __name__ == "__main__":
    main()
