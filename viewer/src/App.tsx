import { useCallback, useEffect, useRef, useState } from 'react';
import { marked } from 'marked';
import { dailies, docs, indexDoc, sessions, type Doc } from './lib';
import { Graph } from './Graph';

type Tab = 'memory' | 'debriefs' | 'sessions' | 'graph';

/** [[entity]] → clickable span; then markdown → HTML. */
function renderMd(md: string): string {
  const linked = md.replace(
    /\[\[([^\]|]+)\]\]/g,
    (_, name: string) =>
      `<span class="wikilink" data-entity="${name.trim()}">${name.trim()}</span>`,
  );
  return marked.parse(linked, { async: false }) as string;
}

function StatusBadge({ doc }: { doc: Doc }) {
  if (doc.kind !== 'session') return null;
  const cls =
    doc.status === 'curated' ? 'badge curated' : 'badge draft';
  return (
    <span className={cls}>
      {doc.status ?? '?'}
      {doc.archived ? ' · archived' : ''}
    </span>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>('memory');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [entityFilter, setEntityFilter] = useState<string | null>(null);
  const [graphDoc, setGraphDoc] = useState<Doc | null>(null);
  const [graphTerm, setGraphTerm] = useState<string | null>(null);
  const [paneW, setPaneW] = useState<number>(() => {
    const v = Number(localStorage.getItem('memgraph.paneW'));
    return v >= 300 && v <= 900 ? v : 440;
  });
  const readerRef = useRef<HTMLDivElement>(null);
  const resizeRef = useRef<{ startX: number; startW: number } | null>(null);

  const selected = docs.find((d) => d.id === selectedId) ?? null;

  const listFor = (base: Doc[]): Doc[] =>
    base.filter((d) => {
      if (entityFilter && !d.entities.includes(entityFilter)) return false;
      if (!query) return true;
      const q = query.toLowerCase();
      return (
        d.title.toLowerCase().includes(q) || d.content.toLowerCase().includes(q)
      );
    });

  // Delegated click handler so rendered-markdown wikilinks work.
  const onProseClick = useCallback((e: React.MouseEvent) => {
    const t = (e.target as HTMLElement).closest('.wikilink');
    if (!t) return;
    const entity = t.getAttribute('data-entity');
    if (!entity) return;
    setEntityFilter(entity);
    setSelectedId(null);
    setTab('debriefs');
  }, []);

  const openDoc = (d: Doc) => {
    setSelectedId(d.id);
    setTab(d.kind === 'session' ? 'sessions' : d.kind === 'daily' ? 'debriefs' : 'memory');
  };

  const onGraphSelect = useCallback((doc: Doc | null, term?: string | null) => {
    setGraphDoc(doc);
    setGraphTerm(term ?? null);
  }, []);

  // scroll the reading pane to (and highlight) the clicked file/fn/repo mention
  useEffect(() => {
    const el = readerRef.current;
    if (!el) return;
    for (const m of el.querySelectorAll('mark.gr-hl'))
      m.replaceWith(document.createTextNode(m.textContent ?? ''));
    if (!graphTerm) return;
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let target: HTMLElement | null = null;
    while (walker.nextNode()) {
      const tn = walker.currentNode as Text;
      const idx = tn.textContent ? tn.textContent.indexOf(graphTerm) : -1;
      if (idx >= 0) {
        const range = document.createRange();
        range.setStart(tn, idx);
        range.setEnd(tn, idx + graphTerm.length);
        const mark = document.createElement('mark');
        mark.className = 'gr-hl';
        try {
          range.surroundContents(mark);
          target = mark;
        } catch {
          /* match spans nodes — skip */
        }
        break;
      }
    }
    target?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [graphDoc, graphTerm]);

  const startResize = (e: React.PointerEvent) => {
    resizeRef.current = { startX: e.clientX, startW: paneW };
    const onResize = (ev: PointerEvent) => {
      const r = resizeRef.current;
      if (!r) return;
      const w = Math.max(300, Math.min(900, r.startW + (r.startX - ev.clientX)));
      setPaneW(w);
      localStorage.setItem('memgraph.paneW', String(w));
    };
    const endResize = () => {
      window.removeEventListener('pointermove', onResize);
      window.removeEventListener('pointerup', endResize);
      resizeRef.current = null;
    };
    window.addEventListener('pointermove', onResize);
    window.addEventListener('pointerup', endResize);
  };

  const reader = (doc: Doc | null, fallback: Doc | null) => {
    const show = doc ?? fallback;
    if (!show) return <div className="empty">nothing here yet</div>;
    return (
      <article className="prose" onClick={onProseClick}>
        <div className="doc-meta">
          <code>{show.path}</code> <StatusBadge doc={show} />
        </div>
        {/* Memory files are trusted local content authored by this system. */}
        <div dangerouslySetInnerHTML={{ __html: renderMd(show.content) }} />
      </article>
    );
  };

  const docList = (base: Doc[]) => (
    <nav className="doc-list">
      {listFor(base).map((d) => (
        <button
          key={d.id}
          className={d.id === selectedId ? 'doc-row active' : 'doc-row'}
          onClick={() => setSelectedId(d.id)}
        >
          <span className="doc-date">{d.date ?? ''}</span>
          <span className="doc-title">{d.title}</span>
          <StatusBadge doc={d} />
        </button>
      ))}
    </nav>
  );

  return (
    <div className="app">
      <header className="topbar">
        <h1>project · memory</h1>
        <div className="tabs" role="tablist">
          {(['memory', 'debriefs', 'sessions', 'graph'] as Tab[]).map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              className={tab === t ? 'tab active' : 'tab'}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </div>
        <input
          className="search"
          placeholder="search memory…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {entityFilter ? (
          <button className="chip" onClick={() => setEntityFilter(null)}>
            [[{entityFilter}]] ✕
          </button>
        ) : null}
      </header>

      {tab === 'memory' && (
        <main className="single">{reader(null, indexDoc)}</main>
      )}

      {tab === 'debriefs' && (
        <main className="split">
          {docList(dailies)}
          {reader(selected?.kind === 'daily' ? selected : null, dailies[0] ?? null)}
        </main>
      )}

      {tab === 'sessions' && (
        <main className="split">
          {docList(sessions)}
          {reader(selected?.kind === 'session' ? selected : null, sessions[0] ?? null)}
        </main>
      )}

      {tab === 'graph' && (
        <main className="graphpane">
          <div className="graph-stage">
            <Graph onOpenDoc={openDoc} onSelectSession={onGraphSelect} />
          </div>
          {graphDoc ? (
            <>
              {/* biome-ignore lint/a11y/noStaticElementInteractions: drag handle */}
              <div className="pane-resizer" onPointerDown={startResize} title="drag to resize" />
              <aside className="graph-reader" style={{ width: paneW }}>
                <div className="gr-head">
                  <span className="doc-date">{graphDoc.date ?? ''}</span>
                  <div className="gr-actions">
                    <button className="gr-btn" onClick={() => openDoc(graphDoc)}>
                      full ↗
                    </button>
                    <button className="gr-btn" onClick={() => setGraphDoc(null)}>
                      ✕
                    </button>
                  </div>
                </div>
                <article className="prose" onClick={onProseClick}>
                  {/* trusted local memory content */}
                  <div
                    ref={readerRef}
                    // biome-ignore lint/security/noDangerouslySetInnerHtml: trusted local markdown
                    dangerouslySetInnerHTML={{ __html: renderMd(graphDoc.content) }}
                  />
                </article>
              </aside>
            </>
          ) : null}
        </main>
      )}
    </div>
  );
}
