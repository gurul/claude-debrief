import { useCallback, useEffect, useMemo, useState } from 'react';
import { type Doc, docs, provenance } from './lib';

// Drillable code-provenance tree: session → repo → file → function.
// Vertically stacked blocks, newest session first — every level is a block
// you click to expand the next level in place. No simulation, no canvas:
// dates read top-to-bottom, and "where did this session touch code" is one
// glance down an indented column. Open branches persist across reloads.

const C = {
  session: '#4c9bd6',
  repo: '#5cb87a',
  file: '#d98a3d',
  fn: '#a68cd8',
} as const;

// new key: the old force-graph ids ('r:doc:repo') don't match tree ids
const LS_EXPANDED = 'memtree.expanded';
const load = <T,>(k: string, fb: T): T => {
  try {
    const v = localStorage.getItem(k);
    return v ? (JSON.parse(v) as T) : fb;
  } catch {
    return fb;
  }
};
const save = (k: string, v: unknown) => {
  try {
    localStorage.setItem(k, JSON.stringify(v));
  } catch {
    /* private mode etc. */
  }
};

const base = (p: string): string => p.split('/').pop() ?? p;

// session titles usually open with their own date ("2026-07-21 — …");
// the date already has its own column, so don't print it twice.
const detitle = (title: string, date: string | null): string =>
  date ? title.replace(new RegExp(`^${date}\\s*[—–:-]?\\s*`), '') : title;

export function Graph({
  onOpenDoc,
  onSelectSession,
}: {
  onOpenDoc: (d: Doc) => void;
  onSelectSession?: (d: Doc | null, term?: string | null) => void;
}) {
  const sessions = useMemo(
    () =>
      [...provenance.sessions].sort((a, b) =>
        (b.date ?? '').localeCompare(a.date ?? ''),
      ),
    [],
  );
  const maxChurn = useMemo(() => {
    let m = 1;
    for (const s of sessions)
      for (const r of s.repos)
        for (const f of r.files) m = Math.max(m, f.adds + f.dels);
    return m;
  }, [sessions]);

  // Spread instantly: first visit (no saved state) opens every session and
  // repo so the whole tree reads at a glance — functions stay click-to-reveal.
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    const saved = load<string[] | null>(LS_EXPANDED, null);
    if (saved) return new Set(saved);
    const all = new Set<string>();
    for (const s of provenance.sessions) {
      all.add(`s:${s.docId}`);
      for (const r of s.repos) all.add(`s:${s.docId}/r:${r.repo}`);
    }
    return all;
  });
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => save(LS_EXPANDED, [...expanded]), [expanded]);

  const toggle = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        // collapsing also forgets any open descendants (their ids are prefixed)
        for (const x of next) if (x.startsWith(id)) next.delete(x);
      } else next.add(id);
      return next;
    });
  }, []);

  // selection drives the reading pane: session → its debrief doc; deeper
  // nodes also pass their label so the pane scrolls to the mention.
  const select = useCallback(
    (id: string, docId: string, term: string | null) => {
      setSel(id);
      if (!onSelectSession) return;
      const doc = docs.find((d) => d.id === docId) ?? null;
      onSelectSession(doc, term);
    },
    [onSelectSession],
  );

  const expandAll = () => {
    const next = new Set<string>();
    for (const s of sessions) {
      next.add(`s:${s.docId}`);
      for (const r of s.repos) next.add(`s:${s.docId}/r:${r.repo}`);
    }
    setExpanded(next); // stop at files — functions stay click-to-reveal
  };
  const collapseAll = () => {
    setExpanded(new Set());
    setSel(null);
    onSelectSession?.(null, null);
  };

  return (
    <div className="gtree">
      <div className="gtree-bar">
        <span className="gtree-legend">
          {(Object.keys(C) as (keyof typeof C)[]).map((t) => (
            <span key={t}>
              <i style={{ background: C[t] }} /> {t === 'fn' ? 'function' : t}
            </span>
          ))}
        </span>
        <button className="gd-open" onClick={expandAll}>
          expand repos
        </button>
        <button className="gd-open" onClick={collapseAll}>
          collapse all
        </button>
      </div>

      <div className="gtree-scroll">
        {sessions.map((s) => {
          const sid = `s:${s.docId}`;
          const open = expanded.has(sid);
          const doc = docs.find((d) => d.id === s.docId);
          const files = s.repos.reduce((n, r) => n + r.fileCount, 0);
          return (
            <section key={sid} className="gt-session">
              <div
                className={sel === sid ? 'gt-block sel' : 'gt-block'}
                style={{ borderLeftColor: C.session }}
              >
                <button
                  className="gt-head"
                  aria-expanded={open}
                  onClick={() => {
                    toggle(sid);
                    select(sid, s.docId, null);
                  }}
                >
                  <span className="gt-caret">{open ? '▾' : '▸'}</span>
                  <span className="gt-date">{s.date ?? '—'}</span>
                  <span className="gt-title">{detitle(s.title, s.date)}</span>
                  <span className="gt-meta">
                    {s.repos.length} repo{s.repos.length === 1 ? '' : 's'} · {files} file
                    {files === 1 ? '' : 's'}
                  </span>
                </button>
                {doc ? (
                  <button className="gd-open gt-openbtn" onClick={() => onOpenDoc(doc)}>
                    open ↗
                  </button>
                ) : null}
              </div>

              {open
                ? s.repos.map((r) => {
                    const rid = `${sid}/r:${r.repo}`;
                    const ropen = expanded.has(rid);
                    return (
                      <div key={rid} className="gt-indent">
                        <div
                          className={sel === rid ? 'gt-block sel' : 'gt-block'}
                          style={{ borderLeftColor: C.repo }}
                        >
                          <button
                            className="gt-head"
                            aria-expanded={ropen}
                            onClick={() => {
                              toggle(rid);
                              select(rid, s.docId, r.repo);
                            }}
                          >
                            <span className="gt-caret">{ropen ? '▾' : '▸'}</span>
                            <span className="gt-title mono">{r.repo}</span>
                            <span className="gt-meta">
                              {r.fileCount} files · {r.fnCount} fns
                              {s.prs[r.repo]?.length
                                ? ` · PR ${s.prs[r.repo].join(', ')}`
                                : ''}
                            </span>
                          </button>
                        </div>

                        {ropen
                          ? r.files.map((f) => {
                              const fid = `${rid}/f:${f.path}`;
                              const fopen = expanded.has(fid);
                              const churn = f.adds + f.dels;
                              return (
                                <div key={fid} className="gt-indent">
                                  <div
                                    className={sel === fid ? 'gt-block sel' : 'gt-block'}
                                    style={{ borderLeftColor: C.file }}
                                  >
                                    <button
                                      className="gt-head"
                                      aria-expanded={fopen}
                                      disabled={!f.functions.length}
                                      onClick={() => {
                                        if (f.functions.length) toggle(fid);
                                        select(fid, s.docId, base(f.path));
                                      }}
                                    >
                                      <span className="gt-caret">
                                        {f.functions.length ? (fopen ? '▾' : '▸') : '·'}
                                      </span>
                                      <span className="gt-title mono">{base(f.path)}</span>
                                      <span className="gt-path">{f.path}</span>
                                      <span className="gt-meta">
                                        <em className="adds">+{f.adds}</em>{' '}
                                        <em className="dels">-{f.dels}</em>
                                      </span>
                                      <span
                                        className="gt-churn"
                                        style={{
                                          width: `${Math.max(2, (Math.sqrt(churn) / Math.sqrt(maxChurn)) * 60)}px`,
                                          background: C.file,
                                        }}
                                      />
                                    </button>
                                  </div>

                                  {fopen && f.functions.length ? (
                                    <div className="gt-indent gt-fns">
                                      {f.functions.map((fn) => (
                                        <button
                                          key={fn}
                                          className={
                                            sel === `${fid}#${fn}`
                                              ? 'gt-fn sel'
                                              : 'gt-fn'
                                          }
                                          style={{ borderColor: C.fn }}
                                          onClick={() =>
                                            select(`${fid}#${fn}`, s.docId, fn)
                                          }
                                        >
                                          {fn}
                                        </button>
                                      ))}
                                    </div>
                                  ) : null}
                                </div>
                              );
                            })
                          : null}
                      </div>
                    );
                  })
                : null}
            </section>
          );
        })}
      </div>
    </div>
  );
}
