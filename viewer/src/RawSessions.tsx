import { useEffect, useState } from 'react';
import {
  loadRawSession,
  rawByDate,
  rawSessions,
  type RawSession,
  type RawSessionRef,
} from './raw';

function Turn({ t }: { t: RawSession['turns'][number] }) {
  const time = t.at ? new Date(t.at).toLocaleTimeString() : '';
  return (
    <div className={`rw-turn rw-${t.kind}`}>
      <div className="rw-turn-head">
        <span className="rw-who">{t.kind}</span>
        {time ? <span className="rw-at">{time}</span> : null}
        {t.hadThinking ? <span className="rw-chip rw-think">reasoning</span> : null}
        {t.toolNames.map((n, i) => (
          <span key={`${n}-${i}`} className="rw-chip">
            {n}
          </span>
        ))}
      </div>
      {t.text ? <pre className="rw-text">{t.text}</pre> : null}
    </div>
  );
}

export function RawSessions() {
  const dates = [...rawByDate.keys()];
  const [ref, setRef] = useState<RawSessionRef | null>(null);
  const [session, setSession] = useState<RawSession | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!ref) return;
    let live = true;
    setBusy(true);
    setErr(null);
    setSession(null);
    loadRawSession(ref)
      .then((s) => live && setSession(s))
      .catch((e: unknown) => live && setErr(String(e)))
      .finally(() => live && setBusy(false));
    return () => {
      live = false;
    };
  }, [ref]);

  if (!rawSessions.length)
    return (
      <div className="empty">
        no verbatim archives in this store — set DEBRIEF_RAW_ARCHIVE=1 on the
        SessionEnd hook to start keeping them
      </div>
    );

  return (
    <main className="split">
      <nav className="doc-list">
        {dates.map((d) => (
          <div key={d}>
            <div className="rw-date">{d}</div>
            {(rawByDate.get(d) ?? []).map((r) => (
              <button
                key={r.path}
                className={r.path === ref?.path ? 'doc-row active' : 'doc-row'}
                onClick={() => setRef(r)}
              >
                <span className="doc-date">{r.time.replace(/(\d{2})(\d{2})/, '$1:$2')}</span>
                <span className="doc-title">session {r.sid}</span>
              </button>
            ))}
          </div>
        ))}
      </nav>

      <article className="prose rw-pane">
        <div className="rw-warn">
          <strong>Verbatim transcript — unverified.</strong> This is cold storage:
          nothing here has passed the curation gate, so it records what was believed
          at the time, including the wrong turns. Treat it as evidence to check, not
          as memory to trust. It is never indexed and never read by an agent on its
          own initiative.
        </div>

        {!ref ? (
          <div className="empty">pick a session on the left</div>
        ) : busy ? (
          <div className="empty">decompressing {ref.path}…</div>
        ) : err ? (
          <div className="empty">could not read archive — {err}</div>
        ) : session ? (
          <>
            <div className="doc-meta">
              <code>{session.ref.path}</code>
            </div>
            <div className="rw-meta">
              {session.meta.gitBranch ? <span>branch {session.meta.gitBranch}</span> : null}
              {session.meta.cwd ? <span>{session.meta.cwd}</span> : null}
              <span>
                {session.turns.length} turns · {session.lineCount} lines
              </span>
            </div>
            <div className="rw-list">
              {session.turns.map((t, i) => (
                <Turn key={i} t={t} />
              ))}
            </div>
          </>
        ) : null}
      </article>
    </main>
  );
}
