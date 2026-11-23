import React, { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, CATEGORY_LABELS, cls, fmtDate } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { CategoryBadge, Drawer, Empty } from '../components/ui';
import { PageHead } from './Overview';

function diffWords(a, b) {
  const aw = a.split(/(\s+)/);
  const bw = b.split(/(\s+)/);
  const n = Math.max(aw.length, bw.length);
  const out = [];
  for (let i = 0; i < n; i += 1) {
    const x = aw[i] ?? '';
    const y = bw[i] ?? '';
    out.push({ x, y, same: x === y });
  }
  return out;
}

function DiffView({ left, right }) {
  const rows = useMemo(() => diffWords(left, right), [left, right]);
  return (
    <div className="grid gap-2 md:grid-cols-2">
      {['x', 'y'].map((side) => (
        <div key={side} className="rounded-lg border border-line bg-base/50 p-2 text-xs leading-relaxed">
          {rows.map((r, i) => (
            <span key={i} className={cls(r.same ? 'text-ink/80' : side === 'x' ? 'rounded bg-danger/20 text-pink' : 'rounded bg-ok/20 text-ok')}>
              {side === 'x' ? r.x : r.y}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

export default function Campaigns() {
  const { t } = useI18n();
  const [params] = useSearchParams();
  const [items, setItems] = useState([]);
  const [activeId, setActiveId] = useState(params.get('focus'));
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/campaigns').then((r) => {
      setItems(r.items);
      if (!params.get('focus') && r.items[0]) setActiveId(r.items[0].id);
    }).finally(() => setLoading(false));
  }, []);

  const active = items.find((c) => c.id === activeId);
  const [a, b] = active?.members?.slice(0, 2) || [];
  const media = (active?.members || []).filter((m) => m.media_type);

  return (
    <div className="space-y-4">
      <PageHead title={t('campaigns.title')} sub={`${items.length} clusters detected by text / image / identifier similarity`}
        right={<span className="chip font-mono text-[10px]">MinHash · pHash · shared IDs</span>} />

      <div className="grid gap-4 xl:grid-cols-5">
        <div className="card xl:col-span-2 overflow-hidden">
          <ul className="max-h-[75vh] divide-y divide-line/60 overflow-y-auto">
            {loading && <li className="p-4 text-sm text-muted">{t('common.loading')}</li>}
            {items.map((c) => (
              <li key={c.id} className={cls('cursor-pointer px-4 py-3 transition-colors hover:bg-raised/50', c.id === activeId && 'bg-accent/5 border-l-2 border-accent')}
                onClick={() => setActiveId(c.id)}>
                <div className="flex items-center gap-2">
                  <CategoryBadge category={c.category} />
                  <span className="ml-auto font-mono text-[10px] text-muted">{Math.round(c.similarity * 100)}%</span>
                </div>
                <div className="mt-1 text-sm font-medium text-ink">{c.title}</div>
                {c.snippet && <div className="mt-0.5 line-clamp-1 text-[11px] text-ink/60">“{c.snippet}…”</div>}
                <div className="mt-1 flex flex-wrap gap-1">
                  {c.channels.slice(0, 5).map((ch) => <span key={ch} className="chip text-[10px]">@{ch}</span>)}
                  {c.channels.length > 5 && <span className="chip text-[10px]">+{c.channels.length - 5}</span>}
                </div>
                <div className="mt-1 font-mono text-[10px] text-muted">
                  {fmtDate(c.time_span.start)} → {fmtDate(c.time_span.end)} · {c.posting_frequency.toFixed(1)}/day
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="space-y-4 xl:col-span-3">
          {!active && !loading && <Empty>No campaigns yet — ingest data to detect coordination.</Empty>}
          {active && (
            <>
              <section className="card p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-base font-semibold text-ink">{active.title}</h2>
                  <a className="btn btn-primary ml-auto text-xs" href={api.rawUrl(`/report/${active.id}.pdf`)} target="_blank" rel="noreferrer">⤓ {t('common.report')}</a>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-5">
                  {[['Channels', active.channels.length], ['Ads', active.post_count], ['Alerts', active.alert_count || 0], ['Similarity', `${Math.round(active.similarity * 100)}%`], ['Frequency', `${active.posting_frequency.toFixed(1)}/day`]].map(([k, v]) => (
                    <div key={k} className="rounded-lg border border-line bg-base/40 p-2">
                      <div className="text-[10px] uppercase tracking-wider text-muted">{k}</div>
                      <div className="font-mono text-sm text-ink">{v}</div>
                    </div>
                  ))}
                </div>
                <div className="mt-3">
                  <div className="label">Shared identifiers</div>
                  <div className="flex flex-wrap gap-1">
                    {active.shared_identifiers.map((s) => {
                      const [type, value] = s.split(/:(.+)/);
                      return (
                        <a key={s} href={`/graph?identifier=${encodeURIComponent(value)}`} className="chip border-ok/30 bg-ok/10 text-[10px] text-ok hover:border-ok">
                          {type} · {value}
                        </a>
                      );
                    })}
                    {active.shared_identifiers.length === 0 && <span className="text-xs text-muted">none</span>}
                  </div>
                </div>
              </section>

              {a && b && (
                <section className="card p-4">
                  <div className="mb-2 flex items-center justify-between">
                    <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted">Text diff · two mutations</h3>
                    <span className="font-mono text-[10px] text-muted">@{a.channel} vs @{b.channel}</span>
                  </div>
                  <DiffView left={a.text} right={b.text} />
                </section>
              )}

              <section className="card">
                <div className="border-b border-line px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-muted">Timeline</div>
                <ul className="max-h-72 divide-y divide-line/50 overflow-y-auto">
                  {active.members.map((m) => (
                    <li key={m.post_id} className="flex gap-3 px-4 py-2 text-xs">
                      <span className="w-36 shrink-0 font-mono text-[10px] text-muted">{fmtDate(m.timestamp)}</span>
                      <span className="w-40 shrink-0 truncate text-accent">@{m.channel}</span>
                      <span className="line-clamp-2 text-ink/80">{m.text}</span>
                    </li>
                  ))}
                </ul>
              </section>

              {media.length > 0 && (
                <section className="card p-4">
                  <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Media gallery</div>
                  <div className="grid grid-cols-3 gap-2 md:grid-cols-4">
                    {media.slice(0, 8).map((m) => (
                      <a key={m.post_id} href={`/alerts?focus=${m.alert_id}`} className="group relative">
                        {m.media_type === 'image'
                          ? <img src={api.mediaUrl(m.media_path)} className="h-24 w-full rounded-lg border border-line object-cover group-hover:border-accent/60" alt="" />
                          : <div className="grid h-24 place-items-center rounded-lg border border-line bg-raised text-2xl">🔊</div>}
                      </a>
                    ))}
                  </div>
                </section>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
