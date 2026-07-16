import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from 'react';
import {
  type Simulation,
  type SimulationNodeDatum,
  forceCollide,
  forceLink,
  forceManyBody,
  forceRadial,
  forceSimulation,
  forceX,
  forceY,
} from 'd3-force';
import { Graph3D } from './Graph3D';
import { type Doc, docs, provenance } from './lib';

// Drillable code-provenance graph: session → repo → file → function.
// 2D (d3-force SVG, zoom/pan/focus) or 3D (three.js WebGL). Single-click a node
// to expand + open its debrief; DOUBLE-click to focus (zoom to that node's
// subtree, blur everything else); scroll to zoom, drag background to pan, drag a
// node to pin it. Open branches, pins, focus + mode persist across reloads.

const C = {
  session: '#4c9bd6',
  repo: '#5cb87a',
  file: '#d98a3d',
  fn: '#a68cd8',
} as const;
const EDGE = '#3a4046';
const EDGE_HOT = '#8b949c';
const INK = '#e8eaed';
const INK_2 = '#9aa3ab';

const LS_EXPANDED = 'memgraph.expanded';
const LS_PINS = 'memgraph.pins';
const LS_MODE = 'memgraph.mode';
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

type NType = 'session' | 'repo' | 'file' | 'fn';

interface SNode extends SimulationNodeDatum {
  id: string;
  type: NType;
  label: string;
  sub?: string;
  depth: number;
  parentId: string | null;
  childIds: string[];
  r: number;
  docId?: string;
  path?: string;
  prs?: string[];
}

const base = (p: string): string => p.split('/').pop() ?? p;

function buildAll(): Map<string, SNode> {
  const m = new Map<string, SNode>();
  const put = (n: SNode) => m.set(n.id, n);
  for (const s of provenance.sessions) {
    const sid = `s:${s.docId}`;
    const repoIds: string[] = [];
    for (const r of s.repos) {
      const rid = `r:${s.docId}:${r.repo}`;
      repoIds.push(rid);
      const fileIds: string[] = [];
      for (const f of r.files) {
        const fid = `f:${s.docId}:${r.repo}:${f.path}`;
        fileIds.push(fid);
        const fnIds: string[] = [];
        for (const fn of f.functions) {
          const nid = `fn:${fid}:${fn}`;
          fnIds.push(nid);
          put({ id: nid, type: 'fn', label: fn, depth: 3, parentId: fid, childIds: [], r: 4.5 });
        }
        put({
          id: fid,
          type: 'file',
          label: base(f.path),
          sub: `+${f.adds}/-${f.dels}`,
          depth: 2,
          parentId: rid,
          childIds: fnIds,
          r: Math.min(6 + Math.sqrt(Math.max(1, f.adds + f.dels)) * 0.8, 15),
          path: f.path,
        });
      }
      put({
        id: rid,
        type: 'repo',
        label: r.repo,
        sub: `${r.fileCount} files · ${r.fnCount} fns`,
        depth: 1,
        parentId: sid,
        childIds: fileIds,
        r: 10,
        prs: s.prs[r.repo],
      });
    }
    put({
      id: sid,
      type: 'session',
      label: s.date ?? s.title,
      sub: `${s.repos.length} repos`,
      depth: 0,
      parentId: null,
      childIds: repoIds,
      r: 13,
      docId: s.docId,
    });
  }
  return m;
}

export function Graph({
  onOpenDoc,
  onSelectSession,
}: {
  onOpenDoc: (d: Doc) => void;
  onSelectSession?: (d: Doc | null, term?: string | null) => void;
}) {
  const all = useMemo(() => {
    const m = buildAll();
    const pins = load(LS_PINS, {}) as Record<string, { x: number; y: number }>;
    for (const [id, p] of Object.entries(pins)) {
      const n = m.get(id);
      if (n) {
        n.x = p.x;
        n.y = p.y;
        n.fx = p.x;
        n.fy = p.y;
      }
    }
    return m;
  }, []);

  const [mode, setMode] = useState<'2d' | '3d'>(() => load(LS_MODE, '2d'));
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(load(LS_EXPANDED, []) as string[]),
  );
  const [sel, setSel] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [view, setView] = useState({ k: 1, tx: 0, ty: 0 });
  const [, tick] = useReducer((x: number) => x + 1, 0);

  const wrapRef = useRef<HTMLElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const simRef = useRef<Simulation<SNode, undefined> | null>(null);
  const viewRef = useRef(view);
  viewRef.current = view;
  const [size, setSize] = useState({ w: 900, h: 600 });

  useEffect(() => save(LS_MODE, mode), [mode]);
  useEffect(() => save(LS_EXPANDED, [...expanded]), [expanded]);

  const rootSessionRef = useRef<(id: string) => string | null>(() => null);
  useEffect(() => {
    if (!onSelectSession) return;
    const node = sel ? all.get(sel) : null;
    const rid = sel ? rootSessionRef.current(sel) : null;
    const s = rid ? all.get(rid) : null;
    const doc = s?.docId ? (docs.find((d) => d.id === s.docId) ?? null) : null;
    const term = node && node.type !== 'session' ? node.label : null;
    onSelectSession(doc, term);
  }, [sel, onSelectSession, all]);

  const rootSession = useCallback(
    (id: string): string | null => {
      let n: SNode | undefined = all.get(id);
      while (n && n.type !== 'session') n = n.parentId ? all.get(n.parentId) : undefined;
      return n?.id ?? null;
    },
    [all],
  );
  rootSessionRef.current = rootSession;

  const toggleExpand = useCallback(
    (id: string) => {
      const n = all.get(id);
      if (!n) return;
      setSel(id);
      if (!n.childIds.length) return;
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(id)) {
          const drop = (x: string) => {
            next.delete(x);
            all.get(x)?.childIds.forEach(drop);
          };
          drop(id);
        } else next.add(id);
        return next;
      });
    },
    [all],
  );

  const visible = useMemo(() => {
    const vis = new Set<string>();
    const walk = (id: string) => {
      vis.add(id);
      if (expanded.has(id)) all.get(id)?.childIds.forEach(walk);
    };
    for (const n of all.values()) if (n.type === 'session') walk(n.id);
    return vis;
  }, [all, expanded]);

  // the set kept sharp when focusing: ancestors + the node + its visible subtree
  const focusSet = useMemo(() => {
    if (!focusId) return null;
    const s = new Set<string>();
    let a: SNode | undefined = all.get(focusId);
    while (a) {
      s.add(a.id);
      a = a.parentId ? all.get(a.parentId) : undefined;
    }
    const down = (id: string) => {
      s.add(id);
      all.get(id)?.childIds.forEach((c) => {
        if (visible.has(c)) down(c);
      });
    };
    down(focusId);
    return s;
  }, [focusId, all, visible]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const { width, height } = e.contentRect;
      if (width > 40 && height > 40) setSize({ w: width, h: height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (mode !== '2d') return;
    const { w, h } = size;
    const cx = w / 2;
    const cy = h / 2;
    const nodes = [...visible].map((id) => all.get(id) as SNode);
    for (const n of nodes) {
      if (n.x == null || Number.isNaN(n.x)) {
        const p = n.parentId ? all.get(n.parentId) : null;
        n.x = (p?.x ?? cx) + (Math.random() - 0.5) * 40;
        n.y = (p?.y ?? cy) + (Math.random() - 0.5) * 40;
      }
    }
    const links = nodes
      .filter((n) => n.parentId && visible.has(n.parentId))
      .map((n) => ({ source: n.parentId as string, target: n.id }));

    const ring = Math.min(w, h) * 0.15;
    const ringR = [ring * 0.7, ring * 1.7, ring * 2.5, ring * 3.15];
    const linkDist = [110, 96, 70, 52];
    const sim = forceSimulation<SNode>(nodes)
      .velocityDecay(0.5)
      .alphaDecay(0.055)
      .force(
        'link',
        forceLink<SNode, { source: string; target: string }>(links)
          .id((d) => d.id)
          .distance((l) => linkDist[(l.target as unknown as SNode).depth] ?? 70)
          .strength(0.55),
      )
      .force('charge', forceManyBody<SNode>().strength(-280).distanceMax(420))
      .force('collide', forceCollide<SNode>().radius((n) => n.r + 8).iterations(2))
      .force(
        'radial',
        forceRadial<SNode>((n) => ringR[n.depth] ?? ring * 3.15, cx, cy).strength(0.5),
      )
      .force('x', forceX<SNode>(cx).strength(0.03))
      .force('y', forceY<SNode>(cy).strength(0.03))
      .on('tick', () => {
        const pad = 30;
        for (const n of nodes) {
          n.x = Math.max(pad, Math.min(w - pad, n.x as number));
          n.y = Math.max(pad, Math.min(h - pad, n.y as number));
        }
        tick();
      });
    sim.alpha(0.9).restart();
    simRef.current = sim;
    return () => {
      sim.stop();
    };
  }, [visible, size, all, mode]);

  // ── view helpers (zoom / pan / focus) ───────────────────────────────
  const viewAnim = useRef<number | null>(null);
  const animateView = useCallback((target: { k: number; tx: number; ty: number }) => {
    if (viewAnim.current) cancelAnimationFrame(viewAnim.current);
    const from = { ...viewRef.current };
    const start = performance.now();
    const step = (t: number) => {
      const p = Math.min(1, (t - start) / 320);
      const e = 1 - (1 - p) ** 3;
      setView({
        k: from.k + (target.k - from.k) * e,
        tx: from.tx + (target.tx - from.tx) * e,
        ty: from.ty + (target.ty - from.ty) * e,
      });
      if (p < 1) viewAnim.current = requestAnimationFrame(step);
    };
    viewAnim.current = requestAnimationFrame(step);
  }, []);

  const focusNode = useCallback(
    (id: string) => {
      setFocusId(id);
      setSel(id);
      const ids: string[] = [];
      const down = (x: string) => {
        ids.push(x);
        all.get(x)?.childIds.forEach((c) => {
          if (visible.has(c)) down(c);
        });
      };
      down(id);
      const pts = ids.map((x) => all.get(x) as SNode).filter((n) => n.x != null);
      if (!pts.length) return;
      const xs = pts.map((n) => n.x as number);
      const ys = pts.map((n) => n.y as number);
      const minX = Math.min(...xs);
      const maxX = Math.max(...xs);
      const minY = Math.min(...ys);
      const maxY = Math.max(...ys);
      const P = 90;
      const { w, h } = size;
      const bw = Math.max(60, maxX - minX);
      const bh = Math.max(60, maxY - minY);
      const k = Math.max(0.4, Math.min(2.6, Math.min((w - 2 * P) / bw, (h - 2 * P) / bh)));
      animateView({
        k,
        tx: w / 2 - (k * (minX + maxX)) / 2,
        ty: h / 2 - (k * (minY + maxY)) / 2,
      });
    },
    [all, visible, size, animateView],
  );

  const clearFocus = useCallback(() => {
    setFocusId(null);
    animateView({ k: 1, tx: 0, ty: 0 });
  }, [animateView]);

  // native non-passive wheel zoom (around cursor)
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || mode !== '2d') return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = svg.getBoundingClientRect();
      const sx = e.clientX - r.left;
      const sy = e.clientY - r.top;
      setView((v) => {
        const k = Math.max(0.3, Math.min(4, v.k * Math.exp(-e.deltaY * 0.0016)));
        const s = k / v.k;
        return { k, tx: sx - s * (sx - v.tx), ty: sy - s * (sy - v.ty) };
      });
    };
    svg.addEventListener('wheel', onWheel, { passive: false });
    return () => svg.removeEventListener('wheel', onWheel);
  }, [mode]);

  // Esc clears focus
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') clearFocus();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [clearFocus]);

  // background pan
  const panRef = useRef<{ sx: number; sy: number; tx: number; ty: number } | null>(null);
  const onPan = useCallback((e: PointerEvent) => {
    const p = panRef.current;
    if (!p) return;
    setView((v) => ({ ...v, tx: p.tx + (e.clientX - p.sx), ty: p.ty + (e.clientY - p.sy) }));
  }, []);
  const onPanUp = useCallback(() => {
    panRef.current = null;
    window.removeEventListener('pointermove', onPan);
    window.removeEventListener('pointerup', onPanUp);
  }, [onPan]);
  const onSvgDown = (e: React.PointerEvent) => {
    panRef.current = { sx: e.clientX, sy: e.clientY, tx: viewRef.current.tx, ty: viewRef.current.ty };
    window.addEventListener('pointermove', onPan);
    window.addEventListener('pointerup', onPanUp);
  };

  // ── node dragging + click / double-click ────────────────────────────
  const drag = useRef<{ id: string; moved: boolean } | null>(null);
  const clickTimer = useRef<number | null>(null);
  const gXY = (e: PointerEvent | React.PointerEvent) => {
    const r = svgRef.current?.getBoundingClientRect();
    const sx = e.clientX - (r?.left ?? 0);
    const sy = e.clientY - (r?.top ?? 0);
    const v = viewRef.current;
    return { x: (sx - v.tx) / v.k, y: (sy - v.ty) / v.k };
  };
  const onDown = (e: React.PointerEvent, n: SNode) => {
    e.stopPropagation();
    drag.current = { id: n.id, moved: false };
    const { x, y } = gXY(e);
    n.fx = x;
    n.fy = y;
    simRef.current?.alphaTarget(0.3).restart();
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };
  const onMove = (e: PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const n = all.get(d.id);
    if (!n) return;
    const { x, y } = gXY(e);
    if (Math.hypot(x - (n.fx ?? x), y - (n.fy ?? y)) > 3 / viewRef.current.k) d.moved = true;
    n.fx = x;
    n.fy = y;
  };
  const onUp = () => {
    const d = drag.current;
    simRef.current?.alphaTarget(0);
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    if (!d) return;
    const n = all.get(d.id);
    if (d.moved && n) {
      const pins = load<Record<string, { x: number | null | undefined; y: number | null | undefined }>>(
        LS_PINS,
        {},
      );
      pins[d.id] = { x: n.fx, y: n.fy };
      save(LS_PINS, pins);
    } else if (n) {
      // single-click → expand + pane, deferred so a double-click can cancel it
      clickTimer.current = window.setTimeout(() => {
        toggleExpand(n.id);
        clickTimer.current = null;
      }, 240);
      n.fx = null;
      n.fy = null;
    }
    drag.current = null;
  };

  const resetLayout = () => {
    for (const n of all.values()) {
      n.fx = null;
      n.fy = null;
      n.x = undefined;
      n.y = undefined;
    }
    setExpanded(new Set());
    setSel(null);
    setFocusId(null);
    setView({ k: 1, tx: 0, ty: 0 });
    save(LS_PINS, {});
    save(LS_EXPANDED, []);
  };

  const nodes = [...visible].map((id) => all.get(id) as SNode);
  const links = nodes
    .filter((n) => n.parentId && visible.has(n.parentId))
    .map((n) => ({ from: all.get(n.parentId as string) as SNode, to: n }));

  const nbr = useMemo(() => {
    if (!hover) return null;
    const s = new Set<string>([hover]);
    for (const l of links) {
      if (l.from.id === hover) s.add(l.to.id);
      if (l.to.id === hover) s.add(l.from.id);
    }
    return s;
  }, [hover, links]);

  const sessDim = sel && !focusId ? rootSession(sel) : null;
  const detail = sel ? all.get(sel) : null;
  const selSession =
    detail?.type === 'session' ? docs.find((d) => d.id === detail.docId) : null;
  const focusLabel = focusId ? all.get(focusId)?.label : null;

  return (
    <figure className="graph force" ref={wrapRef}>
      {mode === '3d' ? (
        <div className="g3d">
          <Graph3D
            width={size.w}
            height={size.h}
            nodes={nodes.map((n) => ({ id: n.id, type: n.type, label: n.label, r: n.r }))}
            links={links.map((l) => ({ source: l.from.id, target: l.to.id }))}
            onNodeClick={toggleExpand}
          />
        </div>
      ) : (
        <svg
          ref={svgRef}
          width={size.w}
          height={size.h}
          viewBox={`0 0 ${size.w} ${size.h}`}
          role="img"
          aria-label="Code-provenance drill-down"
          onPointerDown={onSvgDown}
          onDoubleClick={() => focusId && clearFocus()}
          style={{ cursor: 'grab' }}
        >
          <g transform={`translate(${view.tx},${view.ty}) scale(${view.k})`}>
            {links.map((l) => {
              const hot = nbr?.has(l.from.id) && nbr?.has(l.to.id);
              const blur = focusSet ? !(focusSet.has(l.from.id) && focusSet.has(l.to.id)) : false;
              return (
                <line
                  key={`${l.from.id}->${l.to.id}`}
                  className={blur ? 'gedge blurred' : 'gedge'}
                  x1={l.from.x}
                  y1={l.from.y}
                  x2={l.to.x}
                  y2={l.to.y}
                  stroke={hot ? EDGE_HOT : EDGE}
                  strokeWidth={hot ? 2 : 1}
                  opacity={blur ? 0.08 : hover && !hot ? 0.15 : 0.8}
                />
              );
            })}
            {nodes.map((n) => {
              const isHot = nbr?.has(n.id) ?? false;
              const inFocus = focusSet ? focusSet.has(n.id) : true;
              let op = 1;
              if (hover) op = isHot ? 1 : 0.22;
              else if (focusSet && !inFocus) op = 0.07;
              else if (sessDim && rootSession(n.id) !== sessDim) op = 0.16;
              const open = expanded.has(n.id);
              const canExpand = n.childIds.length > 0;
              const seld = sel === n.id;
              const showLabel =
                n.type === 'session' ||
                n.type === 'repo' ||
                isHot ||
                seld ||
                (n.type === 'file' && (nodes.length < 28 || inFocus)) ||
                (n.type === 'fn' && (nodes.length < 16 || (focusSet && inFocus)));
              return (
                <g
                  key={n.id}
                  className={focusSet && !inFocus ? 'gnode blurred' : 'gnode'}
                  transform={`translate(${n.x ?? 0},${n.y ?? 0})`}
                  opacity={op}
                  style={{ cursor: 'grab' }}
                  onPointerDown={(e) => onDown(e, n)}
                  onDoubleClick={(e) => {
                    e.stopPropagation();
                    if (clickTimer.current) {
                      window.clearTimeout(clickTimer.current);
                      clickTimer.current = null;
                    }
                    focusNode(n.id);
                  }}
                  onMouseEnter={() => setHover(n.id)}
                  onMouseLeave={() => setHover(null)}
                >
                  <g className="gpop">
                    {n.type === 'session' ? (
                      <rect
                        x={-n.r}
                        y={-n.r}
                        width={n.r * 2}
                        height={n.r * 2}
                        rx={3}
                        fill={C[n.type]}
                        stroke={seld ? INK : '#1a1a19'}
                        strokeWidth={seld ? 2.5 : 2}
                      />
                    ) : n.type === 'repo' ? (
                      <rect
                        x={-n.r}
                        y={-n.r * 0.72}
                        width={n.r * 2}
                        height={n.r * 1.44}
                        rx={n.r}
                        fill={C[n.type]}
                        stroke={seld ? INK : '#1a1a19'}
                        strokeWidth={seld ? 2.5 : 2}
                      />
                    ) : (
                      <circle
                        r={n.r}
                        fill={C[n.type]}
                        stroke={seld ? INK : '#1a1a19'}
                        strokeWidth={seld ? 2.5 : 2}
                      />
                    )}
                    {canExpand && !open ? (
                      <circle
                        r={n.r + 3}
                        fill="none"
                        stroke={C[n.type]}
                        strokeWidth={1}
                        strokeDasharray="2 2"
                        opacity={0.6}
                      />
                    ) : null}
                  </g>
                  {showLabel ? (
                    <text
                      y={n.type === 'session' || n.type === 'repo' ? n.r + 13 : -n.r - 5}
                      textAnchor="middle"
                      fill={isHot || seld ? INK : INK_2}
                      fontSize={n.type === 'fn' ? 9.5 : 11}
                      style={{ pointerEvents: 'none' }}
                    >
                      {n.label}
                    </text>
                  ) : null}
                  <title>
                    {n.type}: {n.label}
                    {n.sub ? ` (${n.sub})` : ''}
                    {canExpand ? ' · click to expand' : ''} · double-click to focus
                  </title>
                </g>
              );
            })}
          </g>
        </svg>
      )}

      <div className="graph-toolbar">
        {focusLabel ? (
          <button className="focus-chip" onClick={clearFocus} title="exit focus (Esc)">
            focus: {focusLabel} ✕
          </button>
        ) : null}
        <div className="mode-toggle">
          <button className={mode === '2d' ? 'on' : ''} onClick={() => setMode('2d')}>
            2D
          </button>
          <button className={mode === '3d' ? 'on' : ''} onClick={() => setMode('3d')}>
            3D
          </button>
        </div>
        <button className="gd-open" onClick={resetLayout}>
          ⟲ reset
        </button>
      </div>

      <div className="graph-detail">
        {detail ? (
          <>
            <span className="gd-type" style={{ color: C[detail.type] }}>
              {detail.type}
            </span>
            <strong>{detail.path ?? detail.label}</strong>
            {detail.sub ? <span className="gd-sub"> · {detail.sub}</span> : null}
            {detail.prs?.length ? (
              <span className="gd-sub"> · PR {detail.prs.join(', ')}</span>
            ) : null}
            {detail.type === 'file' && detail.childIds.length ? (
              <span className="gd-sub">
                {' '}
                · fns: {detail.childIds.map((id) => all.get(id)?.label).join(', ')}
              </span>
            ) : null}
            {detail.childIds.length ? (
              <button className="gd-open" onClick={() => focusNode(detail.id)}>
                ⊙ focus
              </button>
            ) : null}
            {focusId ? (
              <button className="gd-open" onClick={clearFocus}>
                exit focus
              </button>
            ) : null}
            {selSession ? (
              <button className="gd-open" onClick={() => onOpenDoc(selSession)}>
                open debrief ↗
              </button>
            ) : null}
          </>
        ) : (
          <span className="gd-hint">
            click a date to populate · double-click any node to focus · scroll = zoom · drag bg =
            pan
          </span>
        )}
      </div>

      <figcaption className="legend">
        {(['session', 'repo', 'file', 'fn'] as NType[]).map((t) => (
          <span key={t}>
            <svg width="12" height="12">
              {t === 'session' ? (
                <rect width="12" height="12" rx="3" fill={C[t]} />
              ) : t === 'repo' ? (
                <rect y="2" width="12" height="8" rx="4" fill={C[t]} />
              ) : (
                <circle cx="6" cy="6" r={t === 'fn' ? 4 : 6} fill={C[t]} />
              )}
            </svg>{' '}
            {t === 'fn' ? 'function' : t}
          </span>
        ))}
        <span className="legend-note">dashed = expandable · size = churn</span>
      </figcaption>
    </figure>
  );
}
