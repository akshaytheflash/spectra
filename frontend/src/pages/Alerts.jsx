import React, { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, CATEGORY_LABELS, cls, timeAgo } from '../lib/api';
import { useI18n } from '../lib/i18n';
import {
  CategoryBadge, Confidence, DateCell, Drawer, EmojiSignals, FlagSource, RiskBadge, SourceChip, StatusBadge,
} from '../components/ui';
import { PageHead } from './Overview';
import { AlertDetail } from '../components/AlertDetail';

const FILTERS = {
  category: ['', ...Object.keys(CATEGORY_LABELS).filter((k) => k !== 'benign')],
  risk: ['', 'HIGH', 'MEDIUM', 'LOW'],
  status: ['', 'new', 'under_review', 'confirmed', 'dismissed'],
  identifier_type: ['', 'phone', 'upi', 'wallet', 'username', 'location', 'link'],
  source: ['', 'text', 'image_ocr', 'qr', 'voice'],
};

export default function Alerts() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const [filters, setFilters] = useState({
    category: '', risk: '', status: '', identifier_type: '', source: '', channel: '',
    q: params.get('q') || '', date_from: '', date_to: '', sort: 'risk', order: 'desc',
  });
  const [channels, setChannels] = useState([]);
  const [data, setData] = useState({ items: [], total: 0 });
  const [page, setPage] = useState(1);
  const [openId, setOpenId] = useState(params.get('focus'));
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.get('/channels?limit=200').then((r) => setChannels(r.items || [])).catch(() => {});
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const qs = new URLSearchParams({ page: String(page), page_size: '50' });
      Object.entries(filters).forEach(([k, v]) => { if (v) qs.set(k, v); });
      setData(await api.get(`/alerts?${qs}`));
    } finally { setLoading(false); }
  }, [filters, page]);

  useEffect(() => { load(); }, [load]);

  const set = (k) => (e) => { setPage(1); setFilters((f) => ({ ...f, [k]: e.target.value })); };
  const sortBy = (k) => setFilters((f) => ({ ...f, sort: k, order: f.sort === k && f.order === 'desc' ? 'asc' : 'desc' }));

  return (
    <div className="space-y-4">
      <PageHead
        title={t('alerts.title')}
        sub={`${data.total} flagged messages matching the current filters`}
        right={(
          <div className="flex items-center gap-2">
            <span className={cls('text-[11px]', loading ? 'text-accent' : 'text-muted')}>{loading ? 'Refreshing…' : 'Live'}</span>
            <a className="btn" href={api.rawUrl('/identifiers/export.csv')}>Identifiers CSV</a>
          </div>
        )}
      />

      <div className="card grid grid-cols-2 gap-2 p-2.5 md:grid-cols-4 xl:grid-cols-9">
        <input className="input col-span-2" placeholder="Search message text…" value={filters.q} onChange={set('q')} aria-label="Search messages" />
        <select className="input" value={filters.category} onChange={set('category')} aria-label="Category">
          {FILTERS.category.map((c) => <option key={c} value={c}>{c ? CATEGORY_LABELS[c] : 'All categories'}</option>)}
        </select>
        <select className="input" value={filters.risk} onChange={set('risk')} aria-label="Risk">
          {FILTERS.risk.map((c) => <option key={c} value={c}>{c || 'All risk'}</option>)}
        </select>
        <select className="input" value={filters.status} onChange={set('status')} aria-label="Status">
          {FILTERS.status.map((c) => <option key={c} value={c}>{c ? t(`status.${c}`) : 'All status'}</option>)}
        </select>
        <select className="input" value={filters.channel} onChange={set('channel')} aria-label="Channel">
          <option value="">All channels</option>
          {channels.map((c) => <option key={c.id} value={c.username}>@{c.username}</option>)}
        </select>
        <select className="input" value={filters.identifier_type} onChange={set('identifier_type')} aria-label="Identifier type">
          {FILTERS.identifier_type.map((c) => <option key={c} value={c}>{c || 'Any identifier'}</option>)}
        </select>
        <select className="input" value={filters.source} onChange={set('source')} aria-label="Source">
          {FILTERS.source.map((c) => <option key={c} value={c}>{c || 'Any source'}</option>)}
        </select>
        <div className="col-span-2 flex gap-2 xl:col-span-1">
          <input type="date" className="input" value={filters.date_from} onChange={set('date_from')} aria-label="From date" />
          <input type="date" className="input" value={filters.date_to} onChange={set('date_to')} aria-label="To date" />
        </div>
      </div>

      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr>
                {[['risk', 'Risk'], ['category', 'Category'], ['', 'Evidence'], ['channel', 'Channel'],
                  ['confidence', 'Confidence'], ['status', 'Status'], ['timestamp', 'Flagged']].map(([k, label]) => (
                  <th key={label} className="th cursor-pointer select-none hover:text-ink" onClick={() => k && sortBy(k)}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.items.map((a) => (
                <tr
                  key={a.id}
                  className="cursor-pointer hover:bg-raised/40"
                  onClick={() => { setOpenId(a.id); setParams({ focus: a.id }); }}
                >
                  <td className={cls('td whitespace-nowrap risk-spine', `risk-${a.risk}`)}><RiskBadge risk={a.risk} /></td>
                  <td className="td whitespace-nowrap"><CategoryBadge category={a.category} size="xs" /></td>
                  <td className="td max-w-[34rem]">
                    <div className="line-clamp-2 break-words text-[12.5px] text-ink">{a.text}</div>
                    <div className="mt-1 flex flex-wrap items-center gap-1">
                      {(a.source_types || []).map((s) => <SourceChip key={s} source={s} />)}
                      {(a.identifier_types || []).slice(0, 4).map((s) => <span key={s} className="chip font-mono">{s}</span>)}
                      <EmojiSignals items={a.emoji} limit={4} />
                      <FlagSource source={a.flag_source} />
                      {a.cluster_id && <span className="chip border-violet/40 text-violet">campaign</span>}
                    </div>
                  </td>
                  <td className="td whitespace-nowrap font-mono text-[11px] text-muted">@{a.channel}</td>
                  <td className="td"><Confidence value={a.confidence} /></td>
                  <td className="td"><StatusBadge status={a.status} /></td>
                  <td className="td"><DateCell value={a.timestamp} /></td>
                </tr>
              ))}
              {data.items.length === 0 && (
                <tr><td className="td px-4 py-10 text-[13px] text-muted" colSpan={7}>{loading ? t('common.loading') : 'No alerts match these filters.'}</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-line px-3 py-2 text-[11px] text-muted">
          <span>Page {page} · {data.total} rows</span>
          <div className="flex gap-2">
            <button className="btn px-2 py-1" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>Previous</button>
            <button className="btn px-2 py-1" disabled={page * 50 >= data.total} onClick={() => setPage((p) => p + 1)}>Next</button>
          </div>
        </div>
      </div>

      <Drawer open={!!openId} onClose={() => { setOpenId(null); setParams({}); }} title="Alert record" width="max-w-5xl">
        {openId && <AlertDetail id={openId} onChanged={load} />}
      </Drawer>
    </div>
  );
}
