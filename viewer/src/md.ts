import { marked } from 'marked';

// Markdown → HTML, with [[entity]] turned into clickable spans.
//
// Order matters and it used to be wrong: linkifying the RAW markdown injects a
// <span> that marked then escapes when it lands inside backticks, so a claim
// like `[[idempotency-key]]` rendered as literal tag markup. Linkify the
// OUTPUT instead — by then marked has escaped the text, so the brackets are
// safe to match and the span lands in the DOM as a real element. Inline <code>
// is linkified (entity names are code-shaped and are meant to be clickable);
// fenced <pre> blocks are literal samples and are skipped whole.

const WIKILINK = /\[\[([^\]|]+)\]\]/g;
/** Regions of marked's output that must not be rewritten: <pre> and any tag. */
const SKIP = /<pre[\s\S]*?<\/pre>|<[^>]+>/g;

function wikilinks(text: string): string {
  // `name` is already HTML-escaped by marked, so it is inserted verbatim —
  // escaping again would double-encode entity names containing & or <.
  return text.replace(
    WIKILINK,
    (_, name: string) =>
      `<span class="wikilink" data-entity="${name.trim()}">${name.trim()}</span>`,
  );
}

function linkify(html: string): string {
  let out = '';
  let last = 0;
  for (const m of html.matchAll(SKIP)) {
    out += wikilinks(html.slice(last, m.index));
    out += m[0];
    last = m.index + m[0].length;
  }
  return out + wikilinks(html.slice(last));
}

/** Block markdown → HTML. */
export function renderMd(md: string): string {
  return linkify(marked.parse(md, { async: false }) as string);
}

/** Inline markdown → HTML with no wrapping <p>. For one-line claims. */
export function renderInline(md: string): string {
  return linkify(marked.parseInline(md, { async: false }) as string);
}
