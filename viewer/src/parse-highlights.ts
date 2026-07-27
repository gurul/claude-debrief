// ★ starred-layer parsing: HIGHLIGHTS.md → structured entries.
//
// Pure string → structure. No vite globs and no React, so it can be exercised
// standalone. Everything here degrades rather than throws: a file with no
// starred list, no provenance line, or no entries at all yields an empty
// result and the viewer falls back to rendering the raw document.
//
// The face of a card must carry the whole lesson — claim, mechanism AND
// consequence — so the budget is deliberately generous and cuts only ever land
// on a boundary the author wrote (a paragraph break, or a guarded sentence
// end). Sentences are never reordered: what you read on the face is the
// opening of the entry, verbatim and in document order.

/** Where the entry came from. `href` is null when the source is bare text. */
export interface HighlightSource {
  label: string;
  href: string | null;
}

export interface Highlight {
  /** slug of the claim; unique within the file. */
  id: string;
  /** One-line claim (inline markdown — may hold `code` or [[entity]]). */
  claim: string;
  /** Always on the collapsed face. Markdown. */
  lede: string;
  /** Behind the fold. Markdown. '' when the whole body fits the face. */
  rest: string;
  /** true → `lede` overruns the face budget and is CSS-clamped until opened. */
  clamped: boolean;
  /** Fold-control label, derived by COUNTING. Never by classifying rhetoric. */
  foldLabel: string;
  source: HighlightSource | null;
  /** YYYY-MM-DD when the provenance line carries one. */
  starred: string | null;
  /** Body opened with the `*(example)*` marker from the shipped template. */
  isExample: boolean;
  /** lowercased claim + body + source label — drives filtering. */
  searchText: string;
  /** lowercased text NOT visible on the collapsed face — drives force-expand. */
  hiddenText: string;
}

export interface ParsedHighlights {
  entries: Highlight[];
  /**
   * Everything in the file that is not an entry — the intro, the layer table,
   * the two rules, how-to-star, recall. Kept, not deleted: it stops being the
   * page but stays one disclosure away.
   */
  about: string;
}

/** Face budget in characters. ~7 lines at 66ch. */
const LEDE_MAX = 900;
/** Never cut a sentence-split lede shorter than this. */
const LEDE_MIN = 380;
/** A fold that reveals less than this is not worth a control. */
const MIN_REST = 180;

const BOUNDARY = /(?<=[.!?])[ \t]+(?=[A-Z"'`[(])/g;
const ABBREV =
  /\b(?:e\.g|i\.e|vs|etc|cf|approx|Fig|No|Dr|Mr|Mrs|Ms|St|Jr|Sr|al|Inc|Ltd|Co)\.$/i;

function slug(s: string): string {
  const out = s
    .toLowerCase()
    .replace(/`|\[\[|\]\]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60);
  return out || 'entry';
}

/** A cut is only legal where no inline construct is left hanging open. */
function balanced(s: string): boolean {
  if ((s.match(/`/g) ?? []).length % 2 !== 0) return false;
  let square = 0;
  let round = 0;
  for (const ch of s) {
    if (ch === '[') square++;
    else if (ch === ']') square--;
    else if (ch === '(') round++;
    else if (ch === ')') round--;
    if (square < 0 || round < 0) return false;
  }
  return square === 0 && round === 0;
}

/** Largest legal sentence boundary inside the budget, or null if none exists. */
function sentenceCut(body: string): number | null {
  let best: number | null = null;
  for (const m of body.matchAll(BOUNDARY)) {
    const idx = m.index;
    if (idx < LEDE_MIN) continue;
    if (idx > LEDE_MAX) break;
    const head = body.slice(0, idx);
    if (ABBREV.test(head.trimEnd())) continue;
    if (!balanced(head)) continue;
    best = idx;
  }
  return best;
}

function countSentences(s: string): number {
  return (s.match(/[.!?](?=\s|$)/g) ?? []).length || 1;
}

interface BodySplit {
  lede: string;
  rest: string;
  clamped: boolean;
  foldLabel: string;
}

/**
 * Paragraph boundary first (author-controlled), guarded sentence cut second,
 * CSS clamp as the floor that cannot fail. When the whole body fits, it all
 * goes on the face and the card gets no fold control at all — chrome only
 * appears when it has a job.
 */
function splitBody(body: string): BodySplit {
  const whole = (): BodySplit => ({
    lede: body,
    rest: '',
    clamped: body.length > LEDE_MAX,
    foldLabel: body.length > LEDE_MAX ? 'read the rest' : '',
  });
  if (!body) return { lede: '', rest: '', clamped: false, foldLabel: '' };

  const paras = body
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);

  if (paras.length > 1) {
    let n = 1;
    while (
      n < paras.length &&
      paras.slice(0, n + 1).join('\n\n').length <= LEDE_MAX
    )
      n++;
    if (n >= paras.length) return whole();
    const lede = paras.slice(0, n).join('\n\n');
    const rest = paras.slice(n).join('\n\n');
    if (rest.length < MIN_REST) return whole();
    const k = paras.length - n;
    return {
      lede,
      rest,
      clamped: lede.length > LEDE_MAX,
      foldLabel: `${k} more paragraph${k === 1 ? '' : 's'}`,
    };
  }

  if (body.length <= LEDE_MAX) return whole();

  const cut = sentenceCut(body);
  // No legal cut: keep the body intact and let the clamp hold the floor.
  // A mis-cut lede is worse than a clamped one.
  if (cut === null) return whole();

  const lede = body.slice(0, cut).trim();
  const rest = body.slice(cut).trim();
  if (rest.length < MIN_REST) return whole();
  const k = countSentences(rest);
  return {
    lede,
    rest,
    clamped: false,
    foldLabel: `${k} more sentence${k === 1 ? '' : 's'}`,
  };
}

/** Pull `— from [label](href) · starred YYYY-MM-DD` off the body's last line. */
function takeProvenance(body: string): {
  body: string;
  source: HighlightSource | null;
  starred: string | null;
} {
  const lines = body.split('\n');
  let last = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i].trim()) {
      last = i;
      break;
    }
  }
  if (last < 0) return { body, source: null, starred: null };

  const m = /^[—–-]{1,2}\s*from\s+(.+)$/i.exec(lines[last].trim());
  if (!m) return { body, source: null, starred: null };

  // Two independent reads, so a malformed half never drops the other half.
  const tail = m[1];
  const link = /\[([^\]]+)\]\(([^)\s]+)[^)]*\)/.exec(tail);
  const date = /starred\s+(\d{4}-\d{2}-\d{2})/i.exec(tail);
  const label = link
    ? link[1].trim()
    : tail.replace(/\s*·?\s*starred\s+\d{4}-\d{2}-\d{2}\s*$/i, '').trim();

  lines.splice(last, 1);
  return {
    body: lines.join('\n').trim(),
    source: label ? { label, href: link ? link[2] : null } : null,
    starred: date?.[1] ?? null,
  };
}

export function parseHighlights(
  content: string | null | undefined,
): ParsedHighlights {
  if (!content || !content.trim()) return { entries: [], about: '' };

  const about: string[] = [];
  const head = /^##[ \t]+The starred list[ \t]*$/im.exec(content);
  let slice: string;
  let scoped = false;

  if (head) {
    // Amputate the scaffolding in one cut: everything before the heading and
    // everything from the next `## ` onward is not the list.
    scoped = true;
    const from = head.index + head[0].length;
    const next = /^##[ \t]/m.exec(content.slice(from));
    const to = next ? from + next.index : content.length;
    slice = content.slice(from, to);
    about.push(content.slice(0, head.index).trim());
    if (next) about.push(content.slice(to).trim());
  } else {
    // Hand-edited or renamed file: fall back to hunting `### ★` anywhere.
    slice = content;
  }

  // Inside the located section every `###` is an entry, ★ or not. Outside it,
  // the ★ is required — otherwise every heading in the file looks like a claim.
  const chunks = slice.split(scoped ? /^#{2,4}[ \t]+/m : /^#{2,4}[ \t]*★[ \t]+/m);
  const preamble = (chunks.shift() ?? '').trim();
  if (preamble) about.push(preamble);

  const seen = new Map<string, number>();
  const entries: Highlight[] = [];

  for (const chunk of chunks) {
    const nl = chunk.indexOf('\n');
    const claim = (nl === -1 ? chunk : chunk.slice(0, nl))
      .trim()
      .replace(/^★[ \t]*/, '')
      .trim();
    let body = (nl === -1 ? '' : chunk.slice(nl + 1)).trim();
    if (!claim && !body) continue;

    const prov = takeProvenance(body);
    body = prov.body;

    const ex = /^\*\(example\)\*[ \t]*/.exec(body);
    const isExample = ex !== null;
    if (ex) body = body.slice(ex[0].length).replace(/^\s*\n?/, '');

    const split = splitBody(body);

    const base = slug(claim);
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);

    entries.push({
      id: n ? `${base}-${n + 1}` : base,
      claim,
      lede: split.lede,
      rest: split.rest,
      clamped: split.clamped,
      foldLabel: split.foldLabel,
      source: prov.source,
      starred: prov.starred,
      isExample,
      searchText:
        `${claim} ${body} ${prov.source?.label ?? ''}`.toLowerCase(),
      hiddenText: `${split.rest} ${split.clamped ? split.lede : ''}`
        .trim()
        .toLowerCase(),
    });
  }

  // Stable partition, not a re-sort: authored (newest-first) order survives
  // inside each group. Template examples must never sit above a real lesson.
  const ordered = [
    ...entries.filter((e) => !e.isExample),
    ...entries.filter((e) => e.isExample),
  ];

  return { entries: ordered, about: about.filter(Boolean).join('\n\n') };
}
