// Verbatim terminal transcripts — the cold-storage layer.
//
// These are RAW, UNCURATED session logs. Nothing here has passed the curation
// gate, so it records what was *believed and said at the time*, including every
// wrong turn, abandoned theory and half-read file. It is evidence, not memory.
//
// Two properties keep it that way and must not be traded away:
//
//   1. It stays gzipped on disk. debrief-search.py indexes `.md` only, so a
//      `.jsonl.gz` is structurally invisible to retrieval — an agent cannot
//      surface it by accident, and cannot be prompted into it by a stray query.
//      Decompression happens HERE, in the browser, on an explicit human click.
//   2. It is never summarised into the semantic layer by machine. Promotion
//      out of raw is a human act (`/debrief day`), same gate as INDEX.md.
//
// If you are an agent reading this file: do not decompress these archives into
// the working tree, and do not read them unless the human has named a specific
// session and asked you to. Their content is unverified by construction.

const rawUrls = import.meta.glob('../../sessions/raw/**/*.jsonl.gz', {
  query: '?url',
  import: 'default',
  eager: true,
}) as Record<string, string>;

export interface RawSessionRef {
  /** `sessions/raw/2026-07-22/2047-686c00ba.jsonl.gz` */
  path: string;
  date: string;
  /** `2047` */
  time: string;
  /** short session id from the filename */
  sid: string;
  url: string;
}

export interface RawTurn {
  kind: 'user' | 'assistant' | 'system';
  at: string | null;
  /** Rendered text. Thinking blocks are excluded — see toolNames. */
  text: string;
  /** Tool names used in an assistant turn, for a compact trace chip row. */
  toolNames: string[];
  /** True when the turn carried reasoning we deliberately did not render. */
  hadThinking: boolean;
}

export interface RawSession {
  ref: RawSessionRef;
  meta: {
    cwd: string | null;
    gitBranch: string | null;
    version: string | null;
    sessionId: string | null;
  };
  turns: RawTurn[];
  lineCount: number;
}

const FILE_RE = /sessions\/raw\/(\d{4}-\d{2}-\d{2})\/(\d{4})-([^/.]+)\.jsonl\.gz$/;

export const rawSessions: RawSessionRef[] = Object.entries(rawUrls)
  .map(([file, url]) => {
    const rel = file.replace(/^\.\.\/\.\.\//, '');
    const m = FILE_RE.exec(rel);
    if (!m) return null;
    return { path: rel, date: m[1], time: m[2], sid: m[3], url };
  })
  .filter((r): r is RawSessionRef => r !== null)
  .sort((a, b) => (b.date + b.time).localeCompare(a.date + a.time));

export const rawByDate: Map<string, RawSessionRef[]> = (() => {
  const m = new Map<string, RawSessionRef[]>();
  for (const r of rawSessions) {
    const list = m.get(r.date) ?? [];
    list.push(r);
    m.set(r.date, list);
  }
  return m;
})();

/**
 * Vite may or may not serve `.gz` with `Content-Encoding: gzip` — which would
 * mean fetch() already decompressed it. Sniff the magic bytes instead of
 * trusting either behaviour, so this works in dev and in a built bundle.
 */
async function readArchive(url: string): Promise<string> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} fetching archive`);
  const buf = await res.arrayBuffer();
  const bytes = new Uint8Array(buf);
  const isGzip = bytes.length > 1 && bytes[0] === 0x1f && bytes[1] === 0x8b;
  if (!isGzip) return new TextDecoder().decode(bytes);
  if (typeof DecompressionStream === 'undefined')
    throw new Error('this browser cannot decompress gzip (needs DecompressionStream)');
  const stream = new Blob([bytes])
    .stream()
    .pipeThrough(new DecompressionStream('gzip'));
  return new Response(stream).text();
}

function textOfContent(content: unknown): {
  text: string;
  tools: string[];
  thinking: boolean;
} {
  if (typeof content === 'string')
    return { text: content, tools: [], thinking: false };
  if (!Array.isArray(content)) return { text: '', tools: [], thinking: false };
  const parts: string[] = [];
  const tools: string[] = [];
  let thinking = false;
  for (const b of content) {
    if (!b || typeof b !== 'object') continue;
    const block = b as Record<string, unknown>;
    const t = block.type;
    if (t === 'text' && typeof block.text === 'string') parts.push(block.text);
    else if (t === 'thinking') thinking = true;
    else if (t === 'tool_use' && typeof block.name === 'string')
      tools.push(block.name);
    else if (t === 'tool_result') {
      // Deliberately not rendered: tool output is the largest and least
      // readable part of a transcript, and re-reading it is what produces
      // confident wrong conclusions. The tool NAME is the useful signal.
    }
  }
  return { text: parts.join('\n\n'), tools, thinking };
}

const cache = new Map<string, RawSession>();

export async function loadRawSession(ref: RawSessionRef): Promise<RawSession> {
  const hit = cache.get(ref.path);
  if (hit) return hit;

  const raw = await readArchive(ref.url);
  const lines = raw.split('\n').filter((l) => l.trim().length > 0);
  const turns: RawTurn[] = [];
  const meta: RawSession['meta'] = {
    cwd: null,
    gitBranch: null,
    version: null,
    sessionId: null,
  };

  for (const line of lines) {
    let o: Record<string, unknown>;
    try {
      o = JSON.parse(line) as Record<string, unknown>;
    } catch {
      continue; // a truncated tail line must not kill the whole transcript
    }
    if (!meta.cwd && typeof o.cwd === 'string') meta.cwd = o.cwd;
    if (!meta.gitBranch && typeof o.gitBranch === 'string')
      meta.gitBranch = o.gitBranch;
    if (!meta.version && typeof o.version === 'string') meta.version = o.version;
    if (!meta.sessionId && typeof o.sessionId === 'string')
      meta.sessionId = o.sessionId;

    const type = o.type;
    if (type !== 'user' && type !== 'assistant' && type !== 'system') continue;
    const message = o.message as Record<string, unknown> | undefined;
    const { text, tools, thinking } = textOfContent(
      message?.content ?? (typeof o.content === 'string' ? o.content : ''),
    );
    if (!text && tools.length === 0) continue;
    turns.push({
      kind: type,
      at: typeof o.timestamp === 'string' ? o.timestamp : null,
      text,
      toolNames: tools,
      hadThinking: thinking,
    });
  }

  const session: RawSession = { ref, meta, turns, lineCount: lines.length };
  cache.set(ref.path, session);
  return session;
}
