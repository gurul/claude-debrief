import { type MutableRefObject, type ReactElement, useEffect, useMemo, useRef, useState } from 'react';
import ForceGraph3D from 'react-force-graph-3d';
import { Object3D } from 'three';
import SpriteText from 'three-spritetext';

// 3D WebGL force graph (three.js under the hood). Same session→repo→file→fn
// drill-down: click a node to expand/collapse, drag to pin, orbit/zoom with the
// mouse. Node objects are cached by id so positions persist across expands.

export type G3Type = 'session' | 'repo' | 'file' | 'fn';
export interface G3Node {
  id: string;
  type: G3Type;
  label: string;
  r: number;
}
export interface G3Link {
  source: string;
  target: string;
}

type ForceNode = G3Node & {
  x?: number;
  y?: number;
  z?: number;
  fx?: number;
  fy?: number;
  fz?: number;
};

type ForceLink = {
  source: string | ForceNode;
  target: string | ForceNode;
};

type D3Force = {
  strength: (value: number) => D3Force;
  distanceMax: (value: number) => D3Force;
  distance: (fn: (link: ForceLink) => number) => D3Force;
};

type ForceGraphHandle = {
  d3Force: (name: string) => D3Force | undefined;
  zoomToFit: (durationMs: number, padding: number) => void;
};

type ForceGraphData = {
  nodes: ForceNode[];
  links: G3Link[];
};

type ForceGraph3DCompatProps = {
  ref: MutableRefObject<ForceGraphHandle | undefined>;
  width: number;
  height: number;
  backgroundColor: string;
  graphData: ForceGraphData;
  nodeRelSize: number;
  warmupTicks: number;
  cooldownTicks: number;
  d3VelocityDecay: number;
  onNodeHover: (node: ForceNode | null) => void;
  nodeVal: (node: ForceNode) => number;
  nodeColor: (node: ForceNode) => string;
  nodeOpacity: number;
  nodeResolution: number;
  nodeThreeObjectExtend: boolean;
  nodeThreeObject: (node: ForceNode) => Object3D;
  linkColor: () => string;
  linkOpacity: number;
  linkWidth: number;
  enableNodeDrag: boolean;
  onNodeClick: (node: ForceNode) => void;
  onNodeDragEnd: (node: ForceNode) => void;
  onEngineStop: () => void;
  showNavInfo: boolean;
};

const ForceGraph3DCompat = ForceGraph3D as unknown as (
  props: ForceGraph3DCompatProps
) => ReactElement;

const COLORS: Record<G3Type, string> = {
  session: '#4c9bd6',
  repo: '#5cb87a',
  file: '#d98a3d',
  fn: '#a68cd8',
};

export function Graph3D({
  nodes,
  links,
  width,
  height,
  onNodeClick,
}: {
  nodes: G3Node[];
  links: G3Link[];
  width: number;
  height: number;
  onNodeClick: (id: string) => void;
}) {
  const cache = useRef(new Map<string, ForceNode>());
  const fgRef = useRef<ForceGraphHandle | undefined>(undefined);

  // Measure our OWN container — the parent's size state lags on first mount and
  // the three.js renderer doesn't reliably reflow when it catches up, leaving
  // the canvas at its initial (too-small) width.
  const wrapRef = useRef<HTMLDivElement>(null);
  const [dim, setDim] = useState({ w: width, h: height });
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const { width: w, height: h } = e.contentRect;
      if (w > 40 && h > 40) setDim({ w, h });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const data = useMemo(() => {
    const present = new Set(nodes.map((n) => n.id));
    for (const id of [...cache.current.keys()])
      if (!present.has(id)) cache.current.delete(id);
    const ns = nodes.map((n) => {
      const o: ForceNode = cache.current.get(n.id) ?? {
        id: n.id,
        type: n.type,
        label: n.label,
        r: n.r,
      };
      o.type = n.type;
      o.label = n.label;
      o.r = n.r;
      cache.current.set(n.id, o);
      return o;
    });
    return { nodes: ns, links: links.map((l) => ({ ...l })) };
  }, [nodes, links]);

  // tighter clustering (short links, gentle repulsion) so the graph reads as one
  // cohesive shape rather than a sparse cloud
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || !data.nodes.length) return;
    try {
      fg.d3Force('charge')?.strength(-38).distanceMax(140);
      fg.d3Force('link')?.distance((l: ForceLink) => {
        const t = typeof l.target === 'object' ? l.target.type : 'repo';
        return t === 'fn' ? 12 : t === 'file' ? 20 : 34;
      });
    } catch {
      /* force API not ready yet */
    }
  }, [data]);

  const manyNodes = nodes.length > 60;

  return (
    <div ref={wrapRef} style={{ position: 'absolute', inset: 0 }}>
    <ForceGraph3DCompat
      ref={fgRef}
      width={dim.w}
      height={dim.h}
      backgroundColor="#0d0e10"
      graphData={data}
      nodeRelSize={7}
      warmupTicks={14}
      cooldownTicks={160}
      d3VelocityDecay={0.45}
      onNodeHover={(n: ForceNode | null) => {
        document.body.style.cursor = n ? 'pointer' : '';
      }}
      nodeVal={(n: ForceNode) => Math.max(1, (n.r * n.r) / 22)}
      nodeColor={(n: ForceNode) => COLORS[n.type]}
      nodeOpacity={0.95}
      nodeResolution={14}
      nodeThreeObjectExtend={true}
      nodeThreeObject={(n: ForceNode) => {
        // declutter: drop function labels in big graphs (empty object = sphere only)
        if (n.type === 'fn' && manyNodes) return new Object3D();
        const s = new SpriteText(n.label);
        s.color = '#d3d8dd';
        s.textHeight = n.type === 'session' ? 6 : n.type === 'repo' ? 5 : 3.5;
        s.position.set(0, (n.r ?? 6) + 3, 0);
        return s;
      }}
      linkColor={() => '#4a5158'}
      linkOpacity={0.35}
      linkWidth={0.6}
      enableNodeDrag={true}
      onNodeClick={(n: ForceNode) => onNodeClick(n.id)}
      onNodeDragEnd={(n: ForceNode) => {
        n.fx = n.x;
        n.fy = n.y;
        n.fz = n.z;
      }}
      onEngineStop={() => {
        try {
          fgRef.current?.zoomToFit(600, 60);
        } catch {
          /* renderer not ready */
        }
      }}
      showNavInfo={false}
    />
    </div>
  );
}
