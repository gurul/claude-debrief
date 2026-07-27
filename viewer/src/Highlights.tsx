import { Fragment, useMemo, useState } from 'react';
import {
  type Doc,
  docs,
  type Highlight,
  highlights,
  highlightsAbout,
} from './lib';
import { renderInline, renderMd } from './md';

// The ★ page. Each entry is a card whose collapsed face carries the whole
// lesson — claim, mechanism and consequence — because the body is only ever
// folded when it overruns the face budget, and then only on a boundary the
// author wrote. What goes behind the fold is the evidence that proves it, not
// the point itself. Cards with nothing hidden get no fold control at all.

const foldable = (h: Highlight) => h.rest !== '' || h.clamped;

function SourceLine({
  source,
  starred,
  onOpenDoc,
}: {
  source: NonNullable<Highlight['source']>;
  starred: string | null;
  onOpenDoc: (d: Doc) => void;
}) {
  const href = source.href;
  const external = href ? /^[a-z]+:/i.test(href) : false;
  // A relative .md target that resolves in the store becomes a hop into the
  // reader rather than a dead link.
  const local =
    href && !external
      ? (docs.find((d) => d.path === href || d.id === href) ?? null)
      : null;

  return (
    <div className="hl-src">
      from{' '}
      {local ? (
        <button className="hl-link" onClick={() => onOpenDoc(local)}>
          {source.label} ↗
        </button>
      ) : external && href ? (
        <a href={href} target="_blank" rel="noreferrer">
          {source.label} ↗
        </a>
      ) : (
        <span className="hl-srcname">{source.label}</span>
      )}
      {starred ? <span className="hl-when"> · starred {starred}</span> : null}
    </div>
  );
}

function Card({
  h,
  open,
  onToggle,
  onOpenDoc,
}: {
  h: Highlight;
  open: boolean;
  onToggle: () => void;
  onOpenDoc: (d: Doc) => void;
}) {
  const claimId = `hl-${h.id}-claim`;
  const ledeId = `hl-${h.id}-lede`;
  const restId = `hl-${h.id}-rest`;
  const hasFold = foldable(h);

  return (
    <article className={h.isExample ? 'hl-card is-example' : 'hl-card'}>
      <div className="hl-gutter" aria-hidden="true">
        ★
      </div>
      <div className="hl-col">
        <h3
          className="hl-claim"
          id={claimId}
          // trusted local memory content
          // biome-ignore lint/security/noDangerouslySetInnerHtml: trusted local markdown
          dangerouslySetInnerHTML={{ __html: renderInline(h.claim) }}
        />
        {h.isExample ? (
          <div>
            <span className="badge hl-tmpl">template example</span>
          </div>
        ) : null}
        <div
          className="hl-lede"
          id={ledeId}
          data-clamp={h.clamped && !open ? '1' : undefined}
          // biome-ignore lint/security/noDangerouslySetInnerHtml: trusted local markdown
          dangerouslySetInnerHTML={{ __html: renderMd(h.lede) }}
        />
        {hasFold ? (
          <button
            className="hl-fold"
            aria-expanded={open}
            aria-controls={h.rest ? restId : ledeId}
            onClick={onToggle}
          >
            {open ? '⌃ fold' : `⌄ ${h.foldLabel}`}
          </button>
        ) : null}
        {h.rest ? (
          <div
            className="hl-rest"
            id={restId}
            role="region"
            aria-labelledby={claimId}
            hidden={!open}
            // biome-ignore lint/security/noDangerouslySetInnerHtml: trusted local markdown
            dangerouslySetInnerHTML={{ __html: renderMd(h.rest) }}
          />
        ) : null}
        {h.source ? (
          <SourceLine
            source={h.source}
            starred={h.starred}
            onOpenDoc={onOpenDoc}
          />
        ) : null}
      </div>
    </article>
  );
}

export function Highlights({
  query,
  onProseClick,
  onOpenDoc,
}: {
  query: string;
  onProseClick: (e: React.MouseEvent) => void;
  onOpenDoc: (d: Doc) => void;
}) {
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const q = query.trim().toLowerCase();

  // The shipped template examples are scaffolding, not lessons. They exist to
  // show the shape of an entry on an empty store, so they earn their place only
  // until the first real star — after that they are noise in a layer whose whole
  // value is that everything in it is load-bearing.
  const visible = useMemo(() => {
    const real = highlights.filter((h) => !h.isExample);
    return real.length ? real : highlights;
  }, []);

  const shown = useMemo(
    () => (q ? visible.filter((h) => h.searchText.includes(q)) : visible),
    [q, visible],
  );

  // A hit behind the fold must never be invisible: progressive disclosure that
  // silently swallows search results is worse than no disclosure at all.
  const forced = useMemo(
    () =>
      new Set(
        q ? shown.filter((h) => h.hiddenText.includes(q)).map((h) => h.id) : [],
      ),
    [q, shown],
  );

  const anyFold = visible.some(foldable);
  const realCount = visible.filter((h) => !h.isExample).length;

  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    // Delegated so [[entity]] spans inside claims and bodies keep working.
    // biome-ignore lint/a11y/noStaticElementInteractions: wikilink delegate
    <div className="hl-page" onClick={onProseClick}>
      <div className="hl-head">
        <h2>★ highlights</h2>
        <span className="hl-count">
          {realCount
            ? `${realCount} starred · never pruned`
            : 'nothing starred yet — these are the shipped template examples'}
          {q ? ` · showing ${shown.length} of ${visible.length}` : ''}
        </span>
        {anyFold ? (
          <button
            className="hl-allbtn"
            onClick={() =>
              setOpen((prev) =>
                prev.size
                  ? new Set()
                  : new Set(visible.filter(foldable).map((h) => h.id)),
              )
            }
          >
            {open.size ? 'fold all' : 'unfold all'}
          </button>
        ) : null}
      </div>

      {shown.length ? (
        <div className="hl-list">
          {shown.map((h, i) => (
            <Fragment key={h.id}>
              {h.isExample && !shown[i - 1]?.isExample ? (
                <div className="hl-sep">template examples</div>
              ) : null}
              <Card
                h={h}
                open={open.has(h.id) || forced.has(h.id)}
                onToggle={() => toggle(h.id)}
                onOpenDoc={onOpenDoc}
              />
            </Fragment>
          ))}
        </div>
      ) : (
        <div className="empty">no starred entry matches “{query.trim()}”</div>
      )}

      {highlightsAbout ? (
        <details className="hl-about">
          <summary>⌄ how starring works</summary>
          <article className="prose">
            {/* trusted local memory content */}
            <div
              // biome-ignore lint/security/noDangerouslySetInnerHtml: trusted local markdown
              dangerouslySetInnerHTML={{ __html: renderMd(highlightsAbout) }}
            />
          </article>
        </details>
      ) : null}
    </div>
  );
}
