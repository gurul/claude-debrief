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
      code/<NNN>-<name>.patch  each Edit, MultiEdit or NotebookEdit to a design file, as a diff
      code/final/<name>        the file on disk when the session ended (live only)
      code/<repo>.diff         uncommitted and untracked changes to those files (live only)
      artifacts/<NNN>-<sha8>.html          pages the session published
      <any page>.bundled.html  the same page with its local images, styles,
                               scripts and fonts inlined, as one file
      references/<NNN>-<sha8>.md    long text the human pasted in

The session's subagents write their own transcripts beside the main one.
They are read too, and every entry from one names it in "agent".

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
  * Other writers own turns/, screens/ and memory/ in the same folder. This
    script never touches them, and keeps keys it does not know in the manifest.

Usage:
    extract-material.py <transcript.jsonl[.gz]> <material-root> --session <id> [--snapshot]
    extract-material.py --backfill <debrief-dir>
    extract-material.py --selftest

--snapshot allows the end-of-session snapshot. It is taken only when the last
message of the session (main or subagent) is under an hour old.
"""

import base64
import binascii
import difflib
import fnmatch
import gzip
import hashlib
import heapq
import html as htmlmod
import importlib.util
import json
import mimetypes
import os
import posixpath
import re
import struct
import subprocess
import sys
import time
import urllib.parse
import zlib
from datetime import datetime

MAX_IMAGE = 20 * 1024 * 1024
MAX_TEXT = 512 * 1024
MIN_REFERENCE = 2000
MAX_LINKS = 300
FRESH_SECS = 3600
KINDS = ("images", "code", "artifacts", "references", "links")
# Published pages with no copy to keep. Kept so a re-run does not try again
# and the reader knows the page existed.
UNKEPT = "unkept"
# Folders other writers add to a session's material folder. Never touched here.
OTHER_WRITERS = ("turns", "screens", "memory")

# Bundling a page: one asset, one page, one run.
MAX_ASSET = 8 * 1024 * 1024
MAX_BUNDLE = 24 * 1024 * 1024
MAX_RUN_BUNDLES = 96 * 1024 * 1024
MAX_LISTED = 100
ASSET_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".avif": "image/avif", ".bmp": "image/bmp", ".ico": "image/x-icon",
    ".svg": "image/svg+xml", ".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".eot": "application/vnd.ms-fontobject",
}
TEXT_ASSETS = {".svg", ".css", ".js", ".mjs"}
SENSITIVE = re.compile(r"(^|/)(\.git|\.ssh|\.aws|\.gnupg|\.env[^/]*)(/|$)")
PAGE_EXT = (".html", ".htm")

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
# The interactive entry points. An "sdk-*" entry point is a headless run: a
# script or an agent drove it, and nobody sat at it.
HEADLESS = re.compile(r"^sdk")


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


def is_page(path, text=None):
    if path.lower().endswith(PAGE_EXT):
        return True
    return isinstance(text, str) and text.lstrip()[:15].lower().startswith(("<!doctype", "<html"))


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


def parse_ts(ts):
    """Epoch seconds from the transcript's ISO time, or None."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def subagent_transcripts(transcript):
    """[(label, path)] for the session's subagent transcripts, sorted.

    Live layout: <session>.jsonl beside <session>/subagents/agent-*.jsonl, and
    <session>/subagents/workflows/<wf>/agent-*.jsonl for workflow agents. An
    archive keeps the same tree, gzipped, under <stem>/ or <stem>.subagents/.
    journal.jsonl in a workflow folder is the runner's log, not an agent.
    """
    base = transcript
    for ext in (".gz", ".jsonl"):
        if base.endswith(ext):
            base = base[: -len(ext)]
    found = {}
    for top in (os.path.join(base, "subagents"), base + ".subagents", base):
        if not os.path.isdir(top):
            continue
        for root, dirs, names in os.walk(top):
            dirs.sort()
            for name in sorted(names):
                stem = name[:-3] if name.endswith(".gz") else name
                if not (stem.startswith("agent-") and stem.endswith(".jsonl")):
                    continue
                rel = os.path.relpath(os.path.join(root, stem[:-6]), top)
                rel = rel.replace(os.sep, "/")
                if rel.startswith("subagents/"):
                    rel = rel[len("subagents/"):]
                found.setdefault(rel, os.path.join(root, name))
    return sorted(found.items())


def within(real, root):
    return real == root or real.startswith(root.rstrip(os.sep) + os.sep)


def page_roots(path):
    """The trees a page may read assets from: its own folder and its repo.

    A folder that is the home directory or the filesystem root is too broad
    to be a page's own tree, so it is not a root.
    """
    broad = {os.path.realpath(os.path.expanduser("~")), os.path.realpath(os.sep)}
    roots = []
    d = os.path.realpath(os.path.dirname(os.path.abspath(path)))
    if d not in broad:
        roots.append(d)
    top = git(d, "rev-parse", "--show-toplevel") if os.path.isdir(d) else ""
    if top:
        top = os.path.realpath(top)
        if top not in broad and top not in roots:
            roots.append(top)
    return roots


class Bundler:
    """Inline a page's local assets into one file. Fails closed without a redactor.

    Reads only files under `roots`, after resolving symlinks, of a type in
    ASSET_TYPES. http(s), data: and other schemes are left as they are.
    CSS, JS and SVG text goes through the redactor; other assets are bytes.
    """

    def __init__(self, page_path, roots, redact, served=None, served_dir="", cache=None):
        self.base = os.path.dirname(os.path.abspath(page_path))
        self.roots = roots
        self.redact = redact
        self.served = served or {}
        self.served_dir = served_dir
        self.cache = cache if cache is not None else {}
        self.total = 0
        self.report = {"bundled": [], "missing": [], "refused": []}

    def _note(self, kind, ref, why=""):
        rows = self.report[kind]
        if len(rows) >= MAX_LISTED:
            return
        s = ref[:300] + (" (%s)" % why if why else "")
        s, _ = self.redact(s)
        if s not in rows:
            rows.append(s)

    def resolve(self, ref, base):
        """The real path of a local asset, or None. Records why not."""
        ref = htmlmod.unescape(ref.strip())
        low = ref.lower()
        if not ref or ref.startswith("#") or low.startswith("//"):
            return None
        if re.match(r"^[a-z][a-z0-9+.-]*:", low) and not low.startswith("file:"):
            return None  # http(s), data:, mailto: and the like stay as written
        if low.startswith("file:"):
            p = urllib.parse.unquote(urllib.parse.urlsplit(ref).path)
        else:
            p = urllib.parse.unquote(re.split(r"[?#]", ref, 1)[0])
        if not p:
            return None
        cand = None
        if base == self.base and self.served:
            key = posixpath.normpath(posixpath.join(self.served_dir, p)).lstrip("/")
            cand = self.served.get(key)
        if cand is None:
            cand = p if os.path.isabs(p) else os.path.join(base, p)
        real = os.path.realpath(cand)
        if not any(within(real, r) for r in self.roots):
            self._note("refused", ref, "outside the page's tree")
            return None
        if SENSITIVE.search(real):
            self._note("refused", ref, "protected path")
            return None
        ext = os.path.splitext(real)[1].lower()
        if ext not in ASSET_TYPES:
            self._note("refused", ref, "not an asset type")
            return None
        if not os.path.isfile(real):
            self._note("missing", ref)
            return None
        size = os.path.getsize(real)
        if size > MAX_ASSET:
            self._note("refused", ref, "over %d MB" % (MAX_ASSET // 1048576))
            return None
        if self.total + size > MAX_BUNDLE:
            self._note("refused", ref, "page total over %d MB" % (MAX_BUNDLE // 1048576))
            return None
        self.total += size
        self._note("bundled", ref)
        return real

    def _read(self, real):
        if real not in self.cache:
            with open(real, "rb") as f:
                self.cache[real] = f.read(MAX_ASSET + 1)
        return self.cache[real]

    def _text(self, real):
        text = self._read(real).decode("utf-8", errors="replace")
        text, _ = self.redact(text)
        return text

    def data_uri(self, real, depth=0):
        ext = os.path.splitext(real)[1].lower()
        mime = ASSET_TYPES.get(ext) or mimetypes.guess_type(real)[0] or "application/octet-stream"
        if ext == ".css":
            raw = self.css(self._text(real), os.path.dirname(real), depth + 1).encode("utf-8")
        elif ext in TEXT_ASSETS:
            raw = self._text(real).encode("utf-8")
        else:
            raw = self._read(real)
        return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))

    def uri(self, ref, base, depth=0):
        real = self.resolve(ref, base)
        return self.data_uri(real, depth) if real else None

    def css(self, text, base, depth=0):
        if depth > 4:
            return text

        def imp(m):
            ref = m.group(2) if m.group(2) is not None else m.group(4)
            u = self.uri(ref, base, depth)
            return "@import url(%s)" % u if u else m.group(0)

        def url(m):
            u = self.uri(m.group(2), base, depth)
            return "url(%s)" % u if u else m.group(0)

        text = re.sub(r"@import\s+(?:url\(\s*(['\"]?)(.*?)\1\s*\)|(['\"])(.*?)\3)", imp, text)
        return re.sub(r"(?<![\w-])url\(\s*(['\"]?)(.*?)\1\s*\)", url, text)

    def page(self, text):
        base = self.base
        attr = re.compile(r"""([\w:-]+)(\s*=\s*)("([^"]*)"|'([^']*)'|([^\s>"']+))""")

        def value(m):
            return next(v for v in (m.group(4), m.group(5), m.group(6)) if v is not None)

        def attrs(tag):
            return {m.group(1).lower(): value(m) for m in attr.finditer(tag)}

        def swap(tag, names, fn):
            def one(m):
                if m.group(1).lower() not in names:
                    return m.group(0)
                new = fn(value(m))
                if new is None:
                    return m.group(0)
                q = '"' if m.group(4) is not None or m.group(6) is not None else "'"
                return "%s%s%s%s%s" % (m.group(1), m.group(2), q, new, q)
            return attr.sub(one, tag)

        def srcset(v):
            out, changed = [], False
            for part in v.split(","):
                bits = part.strip().split(None, 1)
                if not bits:
                    continue
                u = self.uri(bits[0], base)
                changed |= u is not None
                out.append(" ".join([u or bits[0]] + bits[1:]))
            return ", ".join(out) if changed else None

        def style_attr(v):
            new = self.css(htmlmod.unescape(v), base)
            return new if new != htmlmod.unescape(v) else None

        def link(m):
            tag = m.group(0)
            a = attrs(tag)
            if "stylesheet" in a.get("rel", "").lower().split() and a.get("href"):
                real = self.resolve(a["href"], base)
                if not real:
                    return tag
                css = self.css(self._text(real), os.path.dirname(real)).replace("</style", "<\\/style")
                media = ' media="%s"' % htmlmod.escape(a["media"]) if a.get("media") else ""
                return "<style%s>\n%s\n</style>" % (media, css)
            return swap(tag, {"href"}, lambda v: self.uri(v, base))

        def script(m):
            tag, a = m.group(1), attrs(m.group(1))
            real = self.resolve(a["src"], base) if a.get("src") else None
            if not real:
                return m.group(0)
            js = self._text(real).replace("</script", "<\\/script")
            opened = re.sub(r"""\s+src\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", "", tag, flags=re.I)
            return "%s%s</script>" % (opened, js)

        def media_tag(m):
            tag = swap(m.group(0), {"src", "poster", "href", "xlink:href"}, lambda v: self.uri(v, base))
            return swap(tag, {"srcset"}, srcset)

        def style_block(m):
            return m.group(1) + self.css(m.group(2), base) + m.group(3)

        text = re.sub(r"(<style\b[^>]*>)(.*?)(</style\s*>)", style_block, text, flags=re.S | re.I)
        text = re.sub(r"<link\b[^>]*>", link, text, flags=re.I)
        text = re.sub(r"(<script\b[^>]*>)\s*</script\s*>", script, text, flags=re.I)
        text = re.sub(r"<(?:img|source|video|audio|input|image|embed|track|use)\b[^>]*>", media_tag, text, flags=re.I)
        text = re.sub(r"<[a-z][\w-]*\b[^>]*\bstyle\s*=[^>]*>",
                      lambda m: swap(m.group(0), {"style"}, style_attr), text, flags=re.I)
        return text


class Extractor:
    def __init__(self, out_dir, redact, now=None):
        self.out = out_dir
        self.redact = redact
        self.now = time.time() if now is None else now
        self.manifest = {k: [] for k in KINDS + (UNKEPT,)}
        self.old = {}
        self.old_bytes = None
        mp = os.path.join(out_dir, "manifest.json")
        if os.path.exists(mp):
            try:
                with open(mp, "rb") as f:
                    self.old_bytes = f.read()
                old = json.loads(self.old_bytes)
                if isinstance(old, dict):
                    self.old = old
                    for k in KINDS + (UNKEPT,):
                        self.manifest[k] = list(old.get(k, []))
            except Exception:
                pass
        self.seen = {k: {e.get("key") for e in self.manifest[k]} for k in KINDS + (UNKEPT,)}
        # A page's text stored once per session: a republish of the same bytes
        # points at the first copy.
        self.page_files = {}
        for e in self.manifest["artifacts"]:
            sha = e.get("sha") or (e.get("key") if re.fullmatch(r"[0-9a-f]{64}", str(e.get("key"))) else None)
            if sha and e.get("file"):
                self.page_files.setdefault(sha, e)
        self.tools = {}  # tool_use id -> (name, input)
        self.added = {k: 0 for k in KINDS}
        self.skipped = {"bad_lines": 0, "damaged_images": 0, "oversize": 0, "text_no_redactor": 0}
        self.touched = []  # design paths the session wrote or edited, in order
        self.cwd = None
        self.agent = None  # label of the subagent transcript being read; None is the main one
        self.entrypoint = None
        self.last_activity = None
        self.mtimes = []
        self.content = {}  # path -> its text as the session's own writes left it
        self.pending = []  # pages published by path with no write-time copy
        self.snapshot_taken = False
        self.bundle_cache = {}
        self.bundle_bytes = 0
        self.unlabelled = []

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
        if self.agent and "agent" not in entry:
            entry["agent"] = self.agent
        self.manifest[kind].append(entry)
        if kind in self.added:
            self.added[kind] += 1
        return True

    def _n(self, kind):
        return "%03d" % (len(self.manifest[kind]) + 1)

    def fresh(self):
        """True when the session's last message is under FRESH_SECS old.

        The transcript's mtime is the wrong clock: the harness appends
        bookkeeping records (cost, last prompt) after the last message, and a
        subagent can write after the main transcript did. The last user or
        assistant record across every transcript is when the session last acted.
        """
        last = self.last_activity
        if last is None and self.mtimes:
            last = max(self.mtimes)
        return last is not None and self.now - last < FRESH_SECS

    def bundle(self, entry, page_text, path, served=None, served_dir=""):
        """Write a one-file copy of a stored page beside its text copy."""
        if self.redact is None or not path or self.bundle_bytes >= MAX_RUN_BUNDLES:
            if self.bundle_bytes >= MAX_RUN_BUNDLES:
                entry["bundle"] = {"skipped": "run budget of %d MB spent" % (MAX_RUN_BUNDLES // 1048576)}
            return
        try:
            b = Bundler(path, page_roots(path), self.redact, served, served_dir, self.bundle_cache)
            if not b.roots:
                entry["bundle"] = {"skipped": "the page's folder is too broad to read from"}
                return
            out = b.page(page_text)
        except Exception as e:  # a page that defeats the bundler keeps its text copy
            entry["bundle"] = {"skipped": "bundler failed: %s" % type(e).__name__}
            return
        rep = b.report
        if not any(rep.values()):
            return
        info = {k: v for k, v in rep.items() if v}
        # Labelled once the whole session has been read: see label_bundles().
        info["assets_read"] = None
        self.unlabelled.append(info)
        if rep["bundled"]:
            data = out.encode("utf-8", errors="replace")
            self.bundle_bytes += len(data)
            stem = entry["file"][:-5] if entry["file"].endswith(".html") else entry["file"]
            info["file"] = self._put(os.path.dirname(stem), os.path.basename(stem) + ".bundled.html", data)
            info["bytes"] = len(data)
        entry["bundle"] = info

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

    def _track_edit(self, path, old, new, every=False):
        text = self.content.get(path)
        if text is None:
            return
        if old and old in text:
            self.content[path] = text.replace(old, new) if every else text.replace(old, new, 1)
        else:
            self.content.pop(path, None)

    def tool_use(self, block, line, ts):
        name, inp = block.get("name") or "", block.get("input")
        if not isinstance(inp, dict):
            return
        if block.get("id"):
            self.tools[block["id"]] = (name, inp)
        for k in ("url", "URL"):
            if isinstance(inp.get(k), str):
                self.link(inp[k], name, line)
        path = inp.get("file_path") or inp.get("path") or inp.get("notebook_path") or ""
        if not isinstance(path, str):
            path = ""
        content = inp.get("content")
        lname = name.lower()
        if name == "Write" and path and isinstance(content, str):
            self.content[path] = content
            if is_design(path):
                self.code(path, content, "write", line, ts)
        elif name == "Edit" and path:
            old, new = inp.get("old_string"), inp.get("new_string")
            if isinstance(old, str) and isinstance(new, str):
                self._track_edit(path, old, new, bool(inp.get("replace_all")))
                if is_design(path):
                    diff = "".join(difflib.unified_diff(
                        old.splitlines(True), new.splitlines(True), "a/" + path, "b/" + path, n=2))
                    if diff:
                        self.code(path, diff, "edit", line, ts)
        elif name == "MultiEdit" and path and isinstance(inp.get("edits"), list):
            parts = []
            for e in inp["edits"]:
                if not isinstance(e, dict):
                    continue
                old, new = e.get("old_string"), e.get("new_string")
                if isinstance(old, str) and isinstance(new, str):
                    self._track_edit(path, old, new, bool(e.get("replace_all")))
                    parts.append("".join(difflib.unified_diff(
                        old.splitlines(True), new.splitlines(True), "a/" + path, "b/" + path, n=2)))
            diff = "".join(parts)
            if diff and is_design(path):
                self.code(path, diff, "multiedit", line, ts)
        elif name == "NotebookEdit" and path:
            self.content.pop(path, None)
            new = inp.get("new_source")
            if isinstance(new, str) and is_design(path):
                head = "# cell %s, %s\n" % (inp.get("cell_id", "?"), inp.get("edit_mode") or "replace")
                diff = "".join(difflib.unified_diff([], new.splitlines(True), "a/" + path, "b/" + path, n=2))
                if diff:
                    self.code(path, head + diff, "notebook", line, ts)
        elif name == "Bash" and isinstance(inp.get("command"), str):
            # A shell command may rewrite a file behind the session's back.
            # A path it names no longer has a write-time copy we can trust.
            cmd = inp["command"]
            for p in [p for p in self.content if os.path.basename(p) and os.path.basename(p) in cmd]:
                self.content.pop(p, None)
        if "artifact" in lname or "publish_files" in lname or "publish_version" in lname:
            self.publish(name, inp, line, ts)

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
        patch = kind in ("edit", "multiedit", "notebook")
        name = "%s-%s%s" % (self._n("code"), slug(os.path.basename(path)), ".patch" if patch else "")
        rel = self._put("code", name, data)
        entry = dict(file=rel, path=path, kind=kind, bytes=len(data), line=line, at=ts)
        if not patch and path.lower().endswith(PAGE_EXT):
            self.bundle(entry, data.decode("utf-8", errors="replace"), path)
        self._add("code", key, entry)

    # ---- published pages ----------------------------------------------
    def publish(self, tool, inp, line, ts):
        title = next((inp[k] for k in ("title", "description", "label")
                      if isinstance(inp.get(k), str) and inp[k].strip()), "")
        title = title.strip().splitlines()[0][:200] if title else ""
        content = inp.get("content")
        if isinstance(content, str) and is_page("", content):
            self.page_inline(content, tool, title, line, ts)
        served, pages = {}, []
        files = inp.get("files")
        if isinstance(files, list):  # [{"as": served path, "path": local path}]
            for f in files:
                if isinstance(f, dict) and isinstance(f.get("as"), str) and isinstance(f.get("path"), str):
                    served[f["as"].lstrip("/")] = f["path"]
        elif isinstance(files, dict):  # {served name: local path}
            for k, v in files.items():
                if isinstance(k, str) and isinstance(v, str):
                    served[k.lstrip("/")] = v
        # Only a page is kept. An asset upload or a markdown file passes a
        # file_path too, and is not a published page.
        fp = inp.get("file_path")
        if isinstance(fp, str) and fp.lower().endswith(PAGE_EXT):
            pages.append(("", fp))
        for as_, p in served.items():
            if as_.lower().endswith(PAGE_EXT) and p != fp:
                pages.append((as_, p))
        edits = inp.get("edits")
        for as_, p in pages:
            if isinstance(edits, list) and p == fp:
                for e in edits:
                    if isinstance(e, dict):
                        old = e.get("old_str", e.get("old_string"))
                        new = e.get("new_str", e.get("new_string"))
                        if isinstance(old, str) and isinstance(new, str):
                            self._track_edit(p, old, new)
            key = "publish:%s:%d:%s" % (self.agent or "main", line, as_ or os.path.basename(p))
            if key in self.seen["artifacts"] or key in self.seen[UNKEPT]:
                continue
            meta = dict(tool=tool, title=title, path=p, line=line, at=ts)
            if as_:
                meta["as"] = as_
            if self.agent:
                meta["agent"] = self.agent
            text = self.content.get(p)
            if text is not None and is_page(p, text):
                self.page_copy(key, text, "write-time", meta, served, posixpath.dirname(as_))
            else:
                self.pending.append((key, meta, served, posixpath.dirname(as_)))

    def page_inline(self, html, tool, title, line, ts):
        sha = hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest()
        if sha in self.seen["artifacts"]:
            return
        data = self._text(html)
        if data is None:
            return
        rel = self._put("artifacts", "%s-%s.html" % (self._n("artifacts"), sha[:8]), data)
        entry = dict(file=rel, tool=tool, title=title, copy="inline", sha=sha, bytes=len(data), line=line, at=ts)
        self.page_files.setdefault(sha, entry)
        self._add("artifacts", sha, entry)

    def page_copy(self, key, text, copy, meta, served, served_dir):
        sha = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        entry = dict(meta, copy=copy, sha=sha)
        same = self.page_files.get(sha)
        if same:
            entry.update(file=same["file"], bytes=same.get("bytes"))
            if same.get("bundle"):
                entry["bundle"] = same["bundle"]
        else:
            data = self._text(text)
            if data is None:
                return
            entry["file"] = self._put("artifacts", "%s-%s.html" % (self._n("artifacts"), sha[:8]), data)
            entry["bytes"] = len(data)
            self.bundle(entry, data.decode("utf-8", errors="replace"), meta["path"], served, served_dir)
            self.page_files[sha] = entry
        agent, self.agent = self.agent, meta.get("agent")
        self._add("artifacts", key, entry)
        self.agent = agent

    def label_bundles(self):
        """Say when a bundle's assets were read. Freshness is known only after
        the last transcript line, so it is filled in here, not mid-walk."""
        when = "session end" if self.fresh() else "later"
        for info in self.unlabelled:
            info["assets_read"] = when
        self.unlabelled = []

    def read_pending(self):
        """Pages published by path that the session never wrote: read them from disk now."""
        copy = "session-end" if self.fresh() else "later"
        for key, meta, served, served_dir in self.pending:
            p = meta["path"]
            try:
                if os.path.isfile(p) and os.path.getsize(p) <= MAX_TEXT:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        text = f.read()
                    if is_page(p, text):
                        self.page_copy(key, text, copy, meta, served, served_dir)
                        continue
                why = "not on disk" if not os.path.isfile(p) else "not a page, or too large"
            except OSError:
                why = "unreadable"
            agent, self.agent = self.agent, meta.get("agent")
            self._add(UNKEPT, key, dict(meta, why=why))
            self.agent = agent
        self.pending = []

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
        sub = self.agent is not None
        if self.cwd is None and not sub and isinstance(obj.get("cwd"), str):
            self.cwd = obj["cwd"]
        ts = obj.get("timestamp") if isinstance(obj.get("timestamp"), str) else ""
        if obj.get("type") in ("user", "assistant"):
            t = parse_ts(ts)
            if t is not None and (self.last_activity is None or t > self.last_activity):
                self.last_activity = t
            if not sub and self.entrypoint is None and obj.get("type") == "user" \
                    and isinstance(obj.get("entrypoint"), str):
                self.entrypoint = obj["entrypoint"]
        # A subagent's "user" turns are its parent's prompt, never the human.
        # A message the human sent while the agent was working is queued, not
        # a user turn. Much of their steering arrives this way.
        if obj.get("type") == "queue-operation" and obj.get("operation") == "enqueue" \
                and isinstance(obj.get("content"), str):
            if not sub:
                self.user_text(obj["content"], line, ts)
            return
        msg = obj.get("message")
        if not isinstance(msg, dict):
            return
        role = msg.get("role") or obj.get("type")
        # Only a real user turn carries the human's words. Request blobs repeat
        # the whole context, with skill text and reminders the harness put in.
        human = (not sub and obj.get("type") == "user" and role == "user"
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
                who = ("agent-prompt" if sub else "user") if role == "user" else str(role)
                self.image(b, line, {"from": who})
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

    def _records(self, path, agent):
        """(timestamp, agent, line, record) in file order. A line with no time
        takes the time of the line before it, so the files merge in order."""
        try:
            self.mtimes.append(os.path.getmtime(path))
        except OSError:
            pass
        last = ""
        with open_text(path) as fh:
            for n, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    self.skipped["bad_lines"] += 1
                    continue
                ts = obj.get("timestamp") if isinstance(obj, dict) else None
                if isinstance(ts, str) and ts:
                    last = ts
                yield (last, agent, n, obj)

    def run(self, transcript):
        """Read the main transcript and every subagent transcript, merged by time,
        so a page published after a subagent edited it gets that edit."""
        streams = [self._records(transcript, None)]
        streams += [self._records(p, label) for label, p in subagent_transcripts(transcript)]
        for _ts, agent, n, obj in heapq.merge(*streams, key=lambda r: r[0]):
            self.agent = agent
            try:
                self.feed(obj, n)
            except Exception:
                self.skipped["bad_lines"] += 1
        self.agent = None
        self.read_pending()
        self.label_bundles()

    # ---- the state on disk at session end (live sessions only) ----------
    def snapshot(self):
        if not self.fresh():
            return
        self.snapshot_taken = True
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
                    entry = dict(file=rel, path=path, kind="final", bytes=len(data))
                    if path.lower().endswith(PAGE_EXT):
                        self.bundle(entry, data.decode("utf-8", errors="replace"), path)
                    self._add("code", key, entry)
            top = git(os.path.dirname(path), "rev-parse", "--show-toplevel")
            if top:
                repos.setdefault(top, []).append(path)
        state = []
        for top, paths in repos.items():
            head = git(top, "rev-parse", "HEAD")
            branch = git(top, "rev-parse", "--abbrev-ref", "HEAD")
            state.append({"repo": top, "branch": branch, "head": head})
            real_top = os.path.realpath(top)
            rels = [os.path.relpath(os.path.realpath(p), real_top) for p in paths]
            diff = git(top, "diff", "HEAD", "--", *rels, strip=False) if head else ""
            # `git diff HEAD` does not see a file git has never been told about.
            # A design file the session created and never added is the one most
            # likely to be lost, so it goes in the diff as a new file.
            others = git(top, "ls-files", "--others", "--exclude-standard", "--", *rels)
            for rel in sorted(set(others.splitlines())):
                try:
                    with open(os.path.join(real_top, rel), encoding="utf-8", errors="replace") as f:
                        lines = f.read().splitlines(True)
                except OSError:
                    continue
                if lines and not lines[-1].endswith("\n"):
                    lines[-1] += "\n"
                diff += "diff --git a/%s b/%s\nnew file mode 100644\n" % (rel, rel)
                diff += "".join(difflib.unified_diff([], lines, "/dev/null", "b/" + rel, n=2))
            if diff:
                sha = hashlib.sha256(diff.encode("utf-8", errors="replace")).hexdigest()
                key = "diff:%s:%s" % (top, sha)
                if key not in self.seen["code"]:
                    data = self._text(diff)
                    if data is not None:
                        rel = self._put("code", "%s-%s.diff" % (sha[:8], slug(os.path.basename(top))), data)
                        self._add("code", key, dict(file=rel, path=top, kind="diff", bytes=len(data), head=head,
                                                    untracked=bool(others.strip())))
        if state:
            self.manifest["git"] = state
        self.label_bundles()

    def save(self, session_id, transcript):
        """Write the manifest when there is something to describe.

        Never deletes a file or a folder. Another writer's folder in here
        (turns/, screens/, memory/) counts as something, so the session keeps
        its folder and its manifest says what it was. Unchanged bytes are not
        rewritten.
        """
        others = any(os.path.isdir(os.path.join(self.out, d)) for d in OTHER_WRITERS)
        if not any(self.manifest[k] for k in KINDS + (UNKEPT,)) and not others and not self.old:
            return False
        m = dict(self.old)
        for k in KINDS:
            m[k] = self.manifest[k]
        if self.manifest[UNKEPT]:
            m[UNKEPT] = self.manifest[UNKEPT]
        m["session_id"] = session_id
        if self.cwd:
            m["cwd"] = self.cwd
        if self.manifest.get("git"):
            m["git"] = self.manifest["git"]
        if self.entrypoint:
            m["entrypoint"] = self.entrypoint
            if HEADLESS.match(self.entrypoint) and not m.get("synthetic"):
                m["synthetic"] = "headless"
        m["sources"] = list(m.get("sources") or [])
        if transcript not in m["sources"]:
            m["sources"].append(transcript)
        m["note"] = "Unverified originals. Evidence, not memory. See docs/material.md."
        data = json.dumps(m, indent=1).encode("utf-8")
        if data == self.old_bytes:
            return True
        os.makedirs(self.out, exist_ok=True)
        tmp = os.path.join(self.out, "manifest.json.tmp")
        with open(tmp, "wb") as f:
            f.write(data)
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


def extract(transcript, root, session_id, snapshot=False, redact="load", now=None):
    if redact == "load":
        redact = load_redactor(os.path.dirname(os.path.abspath(__file__)))
    ex = Extractor(os.path.join(root, session_id[:8]), redact, now=now)
    ex.run(transcript)
    if snapshot:
        ex.snapshot()
    ex.save(session_id, transcript)
    return ex


def summary(ex):
    parts = ["%d %s" % (ex.added[k], k) for k in KINDS if ex.added[k]]
    skips = ["%d %s" % (v, k) for k, v in ex.skipped.items() if v]
    tail = " [skipped: " + ", ".join(skips) + "]" if skips else ""
    if ex.entrypoint and HEADLESS.match(ex.entrypoint):
        tail += " [headless]"
    return (", ".join(parts) or "nothing new") + tail


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
        if not os.path.isdir(os.path.join(raw, day)):
            continue
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


def _iso(epoch):
    return datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _tree_state(d):
    """{relpath: (size, mtime_ns, sha)} for every file under d."""
    out = {}
    for root, _dirs, names in os.walk(d):
        for n in names:
            p = os.path.join(root, n)
            st = os.stat(p)
            out[os.path.relpath(p, d)] = (st.st_size, st.st_mtime_ns, hashlib.sha256(open(p, "rb").read()).hexdigest())
    return out


def selftest():
    import contextlib
    import io
    import tempfile

    global MAX_ASSET
    fails = []

    def check(name, ok, detail=""):
        print(("ok " if ok else "FAIL ") + name + (": %s" % (detail,) if detail and not ok else ""))
        if not ok:
            fails.append(name)

    def jsonl(path, records, extra=""):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            for d in records:
                f.write(json.dumps(d) + "\n")
            f.write(extra)

    def tu(i, name, inp):
        return {"type": "tool_use", "id": i, "name": name, "input": inp}

    def asst(ts, *blocks):
        return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": list(blocks)}}

    here = os.path.dirname(os.path.abspath(__file__))
    red, blue, green = _png(), _png(b"\x00\x00\xff"), _png(b"\x00\xff\x00")
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
        td = os.path.realpath(td)
        t = os.path.join(td, "t.jsonl")
        jsonl(t, lines, "{not json\n")
        root = os.path.join(td, "material")
        ex = extract(t, root, "abcdef0123456789")
        out = os.path.join(root, "abcdef01")
        m = json.load(open(os.path.join(out, "manifest.json")))
        rd = lambda rel, o=out: open(os.path.join(o, rel), "rb").read()

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
              and m["artifacts"][0]["copy"] == "inline" and b"study" in rd(m["artifacts"][0]["file"]))
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
        check("headless: an interactive session is not tagged", "synthetic" not in m, m.get("synthetic"))

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
        f2 = os.path.join(repo, "src", "Tile.tsx")
        open(f1, "w").write("export const Card = () => <div>v1</div>\n")
        open(f2, "w").write("export const Tile = () => <b>a</b>\n")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for a in (["init", "-q"], ["add", "."], ["commit", "-qm", "v1"]):
            subprocess.run(["git", "-C", repo, *a], env=env, capture_output=True)
        open(f1, "w").write("export const Card = () => <div>v2 never committed</div>\n")
        open(f2, "w").write("export const Tile = () => <b>multi</b>\n")
        f3 = os.path.join(repo, "src", "Fresh.tsx")
        open(f3, "w").write("export const Fresh = () => <i>never added</i>\n")
        f4 = os.path.join(repo, "src", "BySub.tsx")
        open(f4, "w").write("export const BySub = () => <u>a subagent wrote me</u>\n")
        sess = os.path.join(td, "proj", "11112222-aaaa.jsonl")
        jsonl(sess, [
            asst(_iso(time.time() - 60),
                 tu("e1", "Edit", {"file_path": f1, "old_string": "v1", "new_string": "v2 never committed"}),
                 tu("e2", "MultiEdit", {"file_path": f2, "edits": [{"old_string": "a", "new_string": "mul"},
                                                                   {"old_string": "mul", "new_string": "multi"}]}),
                 tu("e3", "Write", {"file_path": f3, "content": "export const Fresh = () => <i>never added</i>\n"}))])
        jsonl(os.path.join(td, "proj", "11112222-aaaa", "subagents", "agent-s1.jsonl"), [
            asst(_iso(time.time() - 30), tu("s1", "Write", {
                "file_path": f4, "content": "export const BySub = () => <u>a subagent wrote me</u>\n"}))])
        extract(sess, root, "11112222aaaa", snapshot=True)
        o4 = os.path.join(root, "11112222")
        m4 = json.load(open(os.path.join(o4, "manifest.json")))
        finals = {e["path"]: e for e in m4["code"] if e["kind"] == "final"}
        diffs = [e for e in m4["code"] if e["kind"] == "diff"]
        diff = rd(diffs[0]["file"], o4) if diffs else b""
        check("snapshot: the file on disk at the end is kept",
              f1 in finals and b"v2 never committed" in rd(finals[f1]["file"], o4))
        check("snapshot: the uncommitted diff is kept",
              b"+export const Card = () => <div>v2 never committed</div>" in diff)
        check("snapshot: the repo, branch and head are recorded",
              bool(m4.get("git")) and len(m4["git"][0]["head"]) == 40)
        check("multiedit: kept as one patch with every change",
              any(e["kind"] == "multiedit" and b"+mul" in rd(e["file"], o4) and b"+multi" in rd(e["file"], o4)
                  for e in m4["code"]), m4["code"])
        check("snapshot: a file MultiEdit touched is in the final copies and the diff",
              f2 in finals and b"+export const Tile = () => <b>multi</b>" in diff)
        check("snapshot: an untracked design file is in the diff as a new file and kept as a final copy",
              f3 in finals and b"new file mode" in diff and b"+export const Fresh = () => <i>never added</i>" in diff)
        check("snapshot: a file a subagent wrote is in the final copies and the diff",
              f4 in finals and b"a subagent wrote me" in diff)
        sub_w = [e for e in m4["code"] if e["kind"] == "write" and e["path"] == f4]
        check("subagent: its Write names its transcript, and line counts in that file",
              len(sub_w) == 1 and sub_w[0].get("agent") == "agent-s1" and sub_w[0]["line"] == 1, sub_w)

        # Freshness: the clock is the last message, not the file's mtime.
        stale = os.path.join(td, "proj", "33334444-bbbb.jsonl")
        jsonl(stale, [asst(_iso(time.time() - 3 * 3600), tu("x1", "Edit", {
            "file_path": f1, "old_string": "v1", "new_string": "v2 never committed"})),
            {"type": "cost-state", "costs": 1}, {"type": "last-prompt", "lastPrompt": "x"}])
        exs = extract(stale, root, "33334444bbbb", snapshot=True)
        m5 = json.load(open(os.path.join(root, "33334444", "manifest.json")))
        check("freshness: a session whose last message is 3 h old gets no snapshot, though its file was just written",
              not exs.snapshot_taken and not any(e["kind"] in ("final", "diff") for e in m5["code"]))
        live_sub = os.path.join(td, "proj", "55556666-cccc.jsonl")
        jsonl(live_sub, [asst(_iso(time.time() - 3 * 3600), tu("y1", "Edit", {
            "file_path": f1, "old_string": "v1", "new_string": "v2 never committed"}))])
        jsonl(os.path.join(td, "proj", "55556666-cccc", "subagents", "workflows", "wf_1", "agent-w1.jsonl"),
              [asst(_iso(time.time() - 120), tu("y2", "Read", {"file_path": f1}))])
        jsonl(os.path.join(td, "proj", "55556666-cccc", "subagents", "workflows", "wf_1", "journal.jsonl"),
              [{"type": "assistant", "message": {"content": [tu("j", "Write", {"file_path": f1, "content": "journal"})]}}])
        exl = extract(live_sub, root, "55556666cccc", snapshot=True)
        check("freshness: a subagent's recent message keeps the session fresh", exl.snapshot_taken)
        check("subagent: a workflow agent is found and its journal is not an agent",
              [a for a, _ in subagent_transcripts(live_sub)] == ["workflows/wf_1/agent-w1"],
              subagent_transcripts(live_sub))

        # A subagent's user turn is its parent's prompt: never the human's words.
        hs = os.path.join(td, "proj", "77778888-dddd.jsonl")
        jsonl(hs, [{"type": "user", "entrypoint": "sdk-cli", "timestamp": _iso(time.time() - 100),
                    "message": {"role": "user", "content": "run the job"}}])
        jsonl(os.path.join(td, "proj", "77778888-dddd", "subagents", "agent-p1.jsonl"), [
            # No isSidechain flag: the folder alone says it is a subagent's.
            {"type": "user", "timestamp": _iso(time.time() - 90), "message": {"role": "user",
             "content": "see https://parent-prompt.example/x\n" + essay}},
            {"type": "queue-operation", "operation": "enqueue", "content": "https://parent-queue.example/y"},
            asst(_iso(time.time() - 80), tu("p1", "WebFetch", {"url": "https://tool-in-sub.example/z"}))])
        extract(hs, root, "77778888dddd")
        m6 = json.load(open(os.path.join(root, "77778888", "manifest.json")))
        u6 = {e["url"]: e for e in m6["links"]}
        check("subagent: its prompt gives no reference and no human link",
              not m6["references"] and "https://parent-prompt.example/x" not in u6
              and "https://parent-queue.example/y" not in u6, m6)
        check("subagent: a link its tool opened is kept and names the agent",
              u6.get("https://tool-in-sub.example/z", {}).get("agent") == "agent-p1", u6)
        check("headless: an sdk entry point is tagged, and the session is kept",
              m6.get("synthetic") == "headless" and m6.get("entrypoint") == "sdk-cli", m6)

        # Pages published by path, and the bundler.
        site = os.path.join(td, "site")
        os.makedirs(os.path.join(site, "img"))
        os.makedirs(os.path.join(site, "css"))
        os.makedirs(os.path.join(site, "fonts"))
        open(os.path.join(site, "img", "a.png"), "wb").write(green)
        open(os.path.join(site, "fonts", "f.woff2"), "wb").write(b"wOF2fontbytes")
        open(os.path.join(site, "css", "s.css"), "w").write(
            '@import "more.css";\nbody{background:url(../img/a.png)}\n'
            '@font-face{font-family:x;src:url("../fonts/f.woff2") format("woff2")}\n')
        open(os.path.join(site, "css", "more.css"), "w").write("h1{color:red}\n")
        open(os.path.join(site, "app.js"), "w").write("const API_KEY = '%s';\nconsole.log('</script>')\n" % secret)
        open(os.path.join(site, "big.png"), "wb").write(green + b"\x00" * 4096)
        outside = os.path.join(td, "outside.png")
        open(outside, "wb").write(blue)
        os.symlink(outside, os.path.join(site, "img", "link.png"))
        page = ("<!DOCTYPE html><html><head><link rel=\"stylesheet\" href=\"css/s.css\">"
                "<script src=\"app.js\"></script></head><body>"
                "<img src=\"img/a.png\" srcset=\"img/a.png 1x, img/a.png 2x\">"
                "<img src=\"../outside.png\"><img src=\"%s\"><img src=\"file://%s\">"
                "<img src=\"img/link.png\"><img src=\"img/gone.png\"><img src=\"big.png\">"
                "<img src=\"https://cdn.example/x.png\"><div style=\"background:url('img/a.png')\"></div>"
                "<p>v1</p></body></html>") % (outside, outside)
        pg = os.path.join(site, "index.html")
        other = os.path.join(site, "other.html")
        open(other, "w").write("<!DOCTYPE html><html><body>on disk only</body></html>")
        pages = os.path.join(td, "proj", "9999aaaa-eeee.jsonl")
        t0 = time.time() - 300
        jsonl(pages, [
            asst(_iso(t0), tu("w1", "Write", {"file_path": pg, "content": page})),
            asst(_iso(t0 + 1), tu("p1", "mcp__claude-artifacts__publish_files", {
                "title": "Site v1", "files": [{"as": "index.html", "path": pg},
                                              {"as": "img/a.png", "path": os.path.join(site, "img", "a.png")}]})),
            asst(_iso(t0 + 2), tu("w2", "Edit", {"file_path": pg, "old_string": "<p>v1</p>", "new_string": "<p>v2</p>"})),
            asst(_iso(t0 + 3), tu("p2", "Artifact", {"file_path": pg, "description": "Site v2\nmore"})),
            asst(_iso(t0 + 4), tu("p3", "Artifact", {"file_path": other, "description": "Other"})),
            asst(_iso(t0 + 5), tu("p4", "Artifact", {"file_path": os.path.join(site, "nowhere.html")})),
            asst(_iso(t0 + 6), tu("p5", "Artifact", {"action": "quickstart", "intent": "other"}))])
        open(pg, "w").write(page.replace("<p>v1</p>", "<p>v3 on disk</p>"))
        MAX_ASSET = 2048
        try:
            extract(pages, root, "9999aaaaeeee")
        finally:
            MAX_ASSET = 8 * 1024 * 1024
        o7 = os.path.join(root, "9999aaaa")
        m7 = json.load(open(os.path.join(o7, "manifest.json")))
        arts = m7["artifacts"]
        byt = {e["title"]: e for e in arts}
        check("pages: a page published by path is kept, with title, tool and path",
              "Site v1" in byt and byt["Site v1"]["path"] == pg and byt["Site v1"]["tool"].endswith("publish_files")
              and byt["Site v1"].get("as") == "index.html", arts)
        check("pages: each publish keeps the version it published, from the session's own writes",
              byt.get("Site v1", {}).get("copy") == "write-time" and b"<p>v1</p>" in rd(byt["Site v1"]["file"], o7)
              and byt.get("Site v2", {}).get("copy") == "write-time" and b"<p>v2</p>" in rd(byt["Site v2"]["file"], o7))
        check("pages: a page the session never wrote is read from disk, marked session-end",
              byt.get("Other", {}).get("copy") == "session-end" and b"on disk only" in rd(byt["Other"]["file"], o7))
        check("pages: a published page missing from disk is listed as unkept",
              len(m7.get("unkept", [])) == 1 and m7["unkept"][0]["why"] == "not on disk", m7.get("unkept"))
        check("pages: a call that publishes nothing adds nothing", len(arts) == 3, arts)
        bun = byt.get("Site v1", {}).get("bundle", {})
        bdata = rd(bun["file"], o7).decode() if bun.get("file") else ""
        check("bundle: the page, its image, stylesheet, import, font and script are one file",
              "data:image/png;base64," + b64(green) in bdata and "<style>" in bdata and "h1{color:red}" in base64.b64decode(
                  re.search(r"@import url\(data:text/css;base64,([A-Za-z0-9+/=]+)\)", bdata).group(1)).decode()
              and "data:font/woff2;base64," + b64(b"wOF2fontbytes") in bdata and "console.log" in bdata
              and 'src="app.js"' not in bdata, bun)
        check("bundle: srcset and style attributes are inlined",
              bdata.count("data:image/png;base64," + b64(green)) >= 5, bdata.count(b64(green)))
        check("bundle: script text is redacted and cannot close its own tag",
              secret not in bdata and "[REDACTED:" in bdata and "<\\/script>" in bdata)
        check("bundle: a reference outside the tree is refused, by path, absolute path, file:// and symlink",
              b64(blue) not in bdata and sum("outside the page's tree" in r for r in bun.get("refused", [])) == 4,
              bun.get("refused"))
        check("bundle: a missing asset is listed, an oversize one refused, http left alone",
              any("gone.png" in r for r in bun.get("missing", [])) and any("big.png" in r for r in bun.get("refused", []))
              and "https://cdn.example/x.png" in bdata, bun)
        check("bundle: the manifest lists what was bundled", any("img/a.png" in r for r in bun.get("bundled", [])))
        des = [e for e in m7["code"] if e["kind"] == "write" and e["path"] == pg]
        check("bundle: an html design file gets a bundled copy too", bool(des) and des[0].get("bundle", {}).get("file"))
        red_none = Extractor(os.path.join(td, "m3x"), None)
        ent = {"file": "artifacts/001-x.html"}
        red_none.bundle(ent, page, pg)
        check("bundle: no redactor, no bundle", "bundle" not in ent and not os.path.exists(os.path.join(td, "m3x")))

        # Other writers' folders: never touched, and an empty session keeps its folder.
        o8 = os.path.join(root, "aaaa0000")
        for sub, name, body in (("turns", "index.jsonl", "{}\n"), ("screens", "index.jsonl", "{}\n"),
                                ("memory", "index.json", "{}")):
            os.makedirs(os.path.join(o8, sub))
            open(os.path.join(o8, sub, name), "w").write(body)
        before8 = _tree_state(o8)
        t8 = os.path.join(td, "t8.jsonl")
        jsonl(t8, [{"type": "user", "message": {"role": "user", "content": "hi"}}])
        extract(t8, root, "aaaa0000ffff")
        after8 = _tree_state(o8)
        check("other writers: an otherwise empty session keeps its folder and their files",
              os.path.isdir(o8) and all(after8.get(k) == v for k, v in before8.items()), (before8, after8))
        mp = os.path.join(out, "manifest.json")
        mm = json.load(open(mp))
        mm["turns_written_by"] = "another writer"
        json.dump(mm, open(mp, "w"), indent=1)
        os.makedirs(os.path.join(out, "screens"))
        open(os.path.join(out, "screens", "index.jsonl"), "w").write("{}\n")
        s9 = _tree_state(os.path.join(out, "screens"))
        with open(t, "a") as f:
            f.write(json.dumps(asst("t9", tu("u10", "Write", {"file_path": "/w/src/X.tsx", "content": "x"}))) + "\n")
        extract(t, root, "abcdef0123456789")
        mm = json.load(open(mp))
        check("other writers: a key the extractor does not own survives a run, and their files are untouched",
              mm.get("turns_written_by") == "another writer" and _tree_state(os.path.join(out, "screens")) == s9)

        # A re-run adds nothing and rewrites nothing.
        for tr, sid in ((t, "abcdef0123456789"), (pages, "9999aaaaeeee"), (hs, "77778888dddd"), (sess, "11112222aaaa")):
            o = os.path.join(root, sid[:8])
            st = _tree_state(o)
            exr = extract(tr, root, sid, snapshot=(sid == "11112222aaaa"))
            check("re-run %s: adds nothing and rewrites nothing" % sid[:8],
                  sum(exr.added.values()) == 0 and _tree_state(o) == st, (exr.added, set(_tree_state(o)) ^ set(st)))

        # A session with nothing to keep leaves no folder behind.
        t3 = os.path.join(td, "t3.jsonl")
        jsonl(t3, [{"type": "user", "message": {"role": "user", "content": "hi"}}])
        extract(t3, root, "99990000ffff")
        check("an empty session leaves no folder", not os.path.exists(os.path.join(root, "99990000")))

        # Backfill from an archive whose subagents sit beside it, twice.
        store = os.path.join(td, "store")
        rawd = os.path.join(store, "sessions", "raw", "2026-01-01")
        os.makedirs(rawd)
        with gzip.open(os.path.join(rawd, "1200-bbbb1111.jsonl.gz"), "wt") as g:
            g.write(json.dumps(asst("t1", tu("b1", "Write", {"file_path": "/w/a/Main.tsx", "content": "m"}))) + "\n")
        os.makedirs(os.path.join(rawd, "1200-bbbb1111.subagents"))
        with gzip.open(os.path.join(rawd, "1200-bbbb1111.subagents", "agent-z9.jsonl.gz"), "wt") as g:
            g.write(json.dumps(asst("t2", tu("b2", "Write", {"file_path": "/w/a/Sub.tsx", "content": "s"}))) + "\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            backfill(store)
            first = _tree_state(os.path.join(store, "sessions", "material"))
            backfill(store)
        mb = json.load(open(os.path.join(store, "sessions", "material", "bbbb1111", "manifest.json")))
        check("backfill: an archived subagent transcript is read",
              any(e.get("agent") == "agent-z9" for e in mb["code"]), mb["code"])
        check("backfill: a second run adds nothing and rewrites nothing",
              "backfill: 0 new items" in buf.getvalue().splitlines()[-1]
              and _tree_state(os.path.join(store, "sessions", "material")) == first, buf.getvalue())

        check("image check: a truncated PNG is refused", not image_ok(red[:-6], "image/png"))
        check("design set: a component is in, a server file is out, a tokens file is in",
              is_design("a/B.tsx") and not is_design("a/b.py") and is_design("x/design-tokens.json") and is_design("DESIGN.md"))
        check("page roots: the home folder is never a page's tree",
              os.path.realpath(os.path.expanduser("~")) not in page_roots(os.path.expanduser("~/x.html")))
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
        # Called from the SessionEnd hook: report, never raise into it.
        try:
            ex = extract(a[0], a[1], a[3], snapshot="--snapshot" in a[4:])
            print(summary(ex))
        except Exception as e:
            print("error: %s: %s" % (type(e).__name__, e))
        return
    sys.exit(__doc__.strip().split("Usage:")[1].strip())


if __name__ == "__main__":
    main()
