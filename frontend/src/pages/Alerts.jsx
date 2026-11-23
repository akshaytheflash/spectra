import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, CATEGORY_LABELS, cls, fmtDate, RISK_COLORS } from '../lib/api';
import { useI18n } from '../lib/i18n';
import {
  CategoryBadge, Confidence, DateCell, Drawer, HighlightedText, RiskBadge, SourceChip, StatusBadge,
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
    category: '', risk: '', status: '', identifier_type: '', source: '', channel: '', q: '',
    date_from: '', date_to: '', sort: 'risk', order: 'desc',
  });
  const [channels, setChannels] = useState([]);
  const [data, setData] = useState({ items: [], total: 0 });
  const [page, setPage] = useState(1);
  const [openId, setOpenId] = useState(params.get('focus'));
  const [loading, setLoading] = useState(false);

  useEffect(() => { api.get('/channels').then((r) => setChannels(r.channels)).catch(() => {}); }, []);

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
      <PageHead title={t('alerts.title')} sub={`${data.total} alerts matching filters`}
        right={<span className={cls('chip', loading && 'text-accent')}>{loading ? 'loading…' : 'live'}</span>} />

      <div className="card grid grid-cols-2 gap-2 p-3 md:grid-cols-4 xl:grid-cols-9">
        <input className="input col-span-2" placeholder="Free-text search…" value={filters.q} onChange={set('q')} />
        <select className="input" value={filters.category} onChange={set('category')} title={t('common.category')}>
          {FILTERS.category.map((c) => <option key={c} value={c}>{c ? CATEGORY_LABELS[c] : `${t('common.category')}: ${t('common.all')}`}</option>)}
        </select>
        <select className="input" value={filters.risk} onChange={set('risk')} title={t('common.risk')}>
          {FILTERS.risk.map((c) => <option key={c} value={c}>{c || `${t('common.risk')}: ${t('common.all')}`}</option>)}
        </select>
        <select className="input" value={filters.status} onChange={set('status')} title={t('common.status')}>
          {FILTERS.status.map((c) => <option key={c} value={c}>{c ? t(`status.${c}`) : `${t('common.status')}: ${t('common.all')}`}</option>)}
        </select>
        <select className="input" value={filters.channel} onChange={set('channel')} title={t('common.channel')}>
          <option value="">{t('common.channel')}: {t('common.all')}</option>
          {channels.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select className="input" value={filters.identifier_type} onChange={set('identifier_type')} title="Identifier type">
          {FILTERS.identifier_type.map((c) => <option key={c} value={c}>{c || 'Identifier: all'}</option>)}
        </select>
        <select className="input" value={filters.source} onChange={set('source')} title={t('common.source')}>
          {FILTERS.source.map((c) => <option key={c} value={c}>{c || `${t('common.source')}: ${t('common.all')}`}</option>)}
        </select>
        <div className="col-span-2 flex gap-2">
          <input type="date" className="input" value={filters.date_from} onChange={set('date_from')} />
          <input type="date" className="input" value={filters.date_to} onChange={set('date_to')} />
        </div>
      </div>

      <div className="card overflow-hidden">
        <table className="w-full">
          <thead>
            <tr>
              {[['risk', t('common.risk')], ['category', t('common.category')], ['', 'Evidence'], ['channel', t('common.channel')],
                ['confidence', t('common.confidence')], ['status', t('common.status')], ['timestamp', t('common.date')]].map(([k, label]) => (
                  <th key={label} className="th cursor-pointer hover:text-ink" onClick={() => k && sortBy(k)}>{label}</th>
                ))}
            </tr>
          </thead>
          <tbody>
            {data.items.map((a) => (
              <tr key={a.id} className="cursor-pointer transition-colors hover:bg-raised/50" onClick={() => { setOpenId(a.id); setParams({ focus: a.id }); }}>
                <td className="td"><RiskBadge risk={a.risk} /></td>
                <td className="td"><CategoryBadge category={a.category} /></td>
                <td className="td max-w-[32rem]">
                  <div className="line-clamp-2 break-words text-xs text-ink/90">{a.text}</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {a.source_types.map((s) => <SourceChip key={s} source={s} />)}
                    {a.identifier_types.slice(0, 4).map((s) => <span key={s} className="chip font-mono text-[10px]">{s}</span>)}
                    {a.cluster_id && <span className="chip border-violet/40 text-[10px] text-violet">campaign</span>}
                  </div>
                </td>
                <td className="td whitespace-nowrap text-[11px] text-muted">@{a.channel}</td>
                <td className="td"><Confidence value={a.confidence} /></td>
                <td className="td"><StatusBadge status={a.status} /></td>
                <td className="td"><DateCell value={a.timestamp} /></td>
              </tr>
            ))}
            {data.items.length === 0 && <tr><td className="td text-muted" colSpan={7}>{t('common.loading')}</td></tr>}
          </tbody>
        </table>
        <div className="flex items-center justify-between border-t border-line px-3 py-2 text-xs text-muted">
          <span>Page {page} · {data.total} rows</span>
          <div className="flex gap-2">
            <button className="btn px-2 py-1" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>← Prev</button>
            <button className="btn px-2 py-1" disabled={page * 50 >= data.total} onClick={() => setPage((p) => p + 1)}>Next →</button>
          </div>
        </div>
      </div>

      <Drawer open={!!openId} onClose={() => { setOpenId(null); setParams({}); }} title={t('alerts.detail')} width="max-w-5xl">
        {openId && <AlertDetail id={openId} onChanged={load} />}
      </Drawer>
    </div>
  );
}
