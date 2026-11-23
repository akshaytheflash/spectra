import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, cls, NODE_COLORS } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { PageHead } from './Overview';

const TYPE_FILTERS = ['Channel', 'Phone', 'UPI', 'Wallet', 'Username', 'Location', 'Post'];

export default function EntityGraph() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const [cy, setCy] = useState(null);
  const [data, setData] = useState(null);
  const [ident, setIdent] = useState(params.get('identifier') || '');
  const [hops, setHops] = useState(2);
  const [selected, setSelected] = useState(null);
  const [visible, setVisible] = useState(() => new Set(TYPE_FILTERS));
  const [highlightCluster, setHighlightCluster] = useState('');

  // Load cytoscape from node_modules (bundled).
  useEffect(() => {
    (async () => {
      const mod = await import('cytoscape');
      const cytoscape = mod.default;
      const instance = cytoscape({
        container: containerRef.current,
        style: [
          { selector: 'node', style: {
            'background-color': 'data(color)', label: 'data(label)', color: '#cbd5e1', 'font-size': 8,
            'text-valign': 'bottom', 'text-margin-y': 4, width: 'data(size)', height: 'data(size)',
            'border-width': 1, 'border-color': '#070b14', 'text-outline-width': 2, 'text-outline-color': '#070b14' } },
          { selector: 'node[kind="Post"]', style: { label: '', shape: 'ellipse', opacity: 0.85 } },
          { selector: 'node[kind="Channel"]', style: { shape: 'round-rectangle', 'font-weight': 'bold', 'font-size': 9 } },
          { selector: 'edge', style: { width: 0.8, 'line-color': '#24324a', 'curve-style': 'haystack', opacity: 0.7 } },
          { selector: 'node:selected', style: { 'border-width': 2, 'border-color': '#22d3ee' } },
          { selector: '.dimmed', style: { opacity: 0.12 } },
          { selector: '.cluster-hl', style: { 'border-width': 2.5, 'border-color': '#a78bfa' } },
        ],
        layout: { name: 'cose', animate: false, idealEdgeLength: 60, nodeRepulsion: 9000 },
        wheelSensitivity: 0.2,
      });
      instance.on('tap', 'node', (evt) => setSelected(evt.target.data()));
      cyRef.current = instance;
      setCy(instance);
      return () => instance.destroy();
    })();
  }, []);

  const load = useCallback(async () => {
    const qs = ident ? `?identifier=${encodeURIComponent(ident)}&hops=${hops}` : '';
    setData(await api.get(`/graph${qs}`));
  }, [ident, hops]);
  useEffect(() => { load(); }, [load]);

  // Push data into cytoscape and re-layout.
  useEffect(() => {
    if (!cy || !data) return;
    const elems = [
      ...data.nodes.map((n) => ({
        data: { ...n, color: NODE_COLORS[n.kind] || '#64748b',
          size: n.kind === 'Post' ? 9 : n.kind === 'Channel' ? 22 : 15,
          label: n.kind === 'Post' ? '' : (n.label || '').slice(0, 22) },
      })),
      ...data.edges.map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target, kind: e.kind } })),
    ];
    cy.elements().remove();
    cy.add(elems);
    const n = elems.length;
    cy.layout({ name: n > 260 ? 'grid' : 'cose', animate: false, idealEdgeLength: 55, nodeRepulsion: 7000, randomize: true }).run();
    applyFilters();
  }, [cy, data]);

  const applyFilters = () => {
    if (!cy) return;
    cy.nodes().forEach((nd) => {
      const kind = nd.data('kind');
      const show = visible.has(kind) || kind === 'Post' && visible.size > 0;
      nd.style('display', show ? 'element' : 'none');
    });
  };
  useEffect(applyFilters, [visible, cy]);

  // Highlight a cluster's posts + their identifiers.
  useEffect(() => {
    if (!cy) return;
    cy.elements().removeClass('cluster-hl');
    if (highlightCluster) {
      cy.nodes(`[cluster_id = "${highlightCluster}"]`).addClass('cluster-hl');
    }
  }, [highlightCluster, cy, data]);

  const clusters = useMemo(() => {
    const posts = (data?.nodes || []).filter((n) => n.kind === 'Post' && n.cluster_id);
    const seen = new Map();
    posts.forEach((p) => { if (!seen.has(p.cluster_id)) seen.set(p.cluster_id, 0); seen.set(p.cluster_id, seen.get(p.cluster_id) + 1); });
    return [...seen.entries()].map(([id, count]) => ({ id, count }));
  }, [data]);

  const focus = async (val) => { setIdent(val); if (val) setParams({ identifier: val }); else setParams({}); };

  return (
    <div className="space-y-4">
      <PageHead title={t('graph.title')} sub={data ? `${data.meta.node_count ?? data.nodes.length} nodes · ${data.meta.edge_count ?? data.edges.length} edges` : ''} />

      <div className="card flex flex-wrap items-end gap-2 p-3">
        <div className="min-w-[260px] flex-1">
          <label className="label">Identifier (phone / UPI / wallet / @handle)</label>
          <input className="input" placeholder="e.g. +919000011111 or @ravisupply_ncr" value={ident}
            onChange={(e) => setIdent(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && focus(ident)} />
        </div>
        <div>
          <label className="label">Hops</label>
          <select className="input" value={hops} onChange={(e) => setHops(Number(e.target.value))}>
            <option value={1}>1</option><option value={2}>2</option><option value={3}>3</option>
          </select>
        </div>
        <button className="btn btn-primary" onClick={() => focus(ident)}>Focus</button>
        <button className="btn" onClick={() => focus('')}>Reset</button>
        <div className="ml-auto flex flex-wrap gap-1">
          {TYPE_FILTERS.map((k) => (
            <button key={k} className={cls('chip text-[10px]', visible.has(k) ? 'border-accent/40 text-ink' : 'opacity-40')}
              onClick={() => setVisible((s) => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; })}>
              <span className="h-2 w-2 rounded-full" style={{ background: NODE_COLORS[k] }} />{k}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-4">
        <div className="card relative xl:col-span-3" style={{ height: 620 }}>
          <div ref={containerRef} className="h-full w-full" />
          {data?.meta?.found === false && (
            <div className="absolute inset-0 grid place-items-center text-sm text-muted">No node found for “{ident}”.</div>
          )}
        </div>
        <div className="space-y-3">
          <section className="card p-3">
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Linked across 3+ channels</h3>
            <ul className="max-h-64 space-y-1 overflow-y-auto">
              {(data?.meta?.cross_channel || []).map((x) => (
                <li key={x.node}>
                  <button className="w-full rounded-md border border-line bg-base/40 px-2 py-1.5 text-left text-xs hover:border-accent/40"
                    onClick={() => { focus(x.value); setSelected({ ...x, kind: x.type }); }}>
                    <span className="font-mono text-[11px] text-ok">{x.value}</span>
                    <div className="text-[10px] text-muted">{x.channels.length} channels · {x.channels.slice(0, 3).map((c) => `@${c}`).join(', ')}</div>
                  </button>
                </li>
              ))}
              {(data?.meta?.cross_channel || []).length === 0 && <li className="text-xs text-muted">none</li>}
            </ul>
          </section>
          <section className="card p-3">
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Clusters</h3>
            <select className="input text-xs" value={highlightCluster} onChange={(e) => setHighlightCluster(e.target.value)}>
              <option value="">— none —</option>
              {clusters.map((c) => <option key={c.id} value={c.id}>campaign {c.id.slice(0, 8)} ({c.count} posts)</option>)}
            </select>
          </section>
          <section className="card p-3">
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Selected node</h3>
            {selected ? (
              <div className="space-y-1 text-xs">
                <div className="chip font-mono text-[10px]">{selected.kind || selected.type}</div>
                <div className="break-all font-mono text-ink">{selected.label || selected.value || selected.id}</div>
                {selected.channel && <div className="text-muted">channel @{selected.channel}</div>}
                {selected.category && <div className="text-muted">category {selected.category}</div>}
                {selected.channels && <div className="text-muted">{selected.channels.length} channels</div>}
                <button className="btn mt-1 w-full text-[11px]" onClick={() => focus(selected.label || selected.value)}>Expand neighbourhood</button>
              </div>
            ) : <p className="text-xs text-muted">Click a node to inspect it.</p>}
          </section>
        </div>
      </div>
    </div>
  );
}
