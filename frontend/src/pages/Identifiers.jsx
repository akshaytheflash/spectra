import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, cls, fmtDate } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { CategoryBadge, Empty, SourceChip } from '../components/ui';
import { PageHead } from './Overview';

const TYPES = ['', 'phone', 'upi', 'wallet', 'username', 'location', 'bank', 'ifsc', 'link', 'email'];

function RiskPill({ score }) {
  const c = score >= 60 ? '#f43f5e' : score >= 30 ? '#f59e0b' : '#34d399';
  return (
    <div className="flex items-center gap-2">
      <span className="h-1.5 w-20 overflow-hidden rounded-full bg-line">
        <span className="block h-full rounded-full" style={{ width: `${Math.min(100, score)}%`, background: c }} />
      </span>
      <span className="font-mono text-[11px]" style={{ color: c }}>{score.toFixed(1)}</span>
    </div>
  );
}

export default function Identifiers() {
  const { t } = useI18n();
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState('');
  const [type, setType] = useState('');
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState('risk');

  useEffect(() => {
    setLoading(true);
    const qs = new URLSearchParams();
    if (q) qs.set('q', q);
    if (type) qs.set('type', type);
    api.get(`/identifiers?${qs}`).then((r) => setRows(r.items)).finally(() => setLoading(false));
  }, [q, type]);

  const sorted = useMemo(() => {
    const copy = [...rows];
    copy.sort((a, b) => {
      if (sort === 'channels') return b.channel_count - a.channel_count;
      if (sort === 'posts') return b.post_count - a.post_count;
      if (sort === 'recent') return String(b.last_seen).localeCompare(String(a.last_seen));
      return b.risk_score - a.risk_score;
    });
    return copy;
  }, [rows, sort]);

  return (
    <div className="space-y-4">
      <PageHead title={t('identifiers.title')} sub={`${rows.length} identifiers · normalised across channels`}
        right={<a className="btn" href={api.rawUrl('/identifiers/export.csv')} download>⤓ {t('common.export')}</a>} />

      <div className="card flex flex-wrap items-end gap-2 p-3">
        <div className="min-w-[240px] flex-1">
          <label className="label">{t('common.search')}</label>
          <input className="input" placeholder="phone, UPI, wallet, @handle, city (fuzzy)" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div>
          <label className="label">Type</label>
          <select className="input" value={type} onChange={(e) => setType(e.target.value)}>
            {TYPES.map((x) => <option key={x} value={x}>{x || `${t('common.all')}`}</option>)}
          </select>
        </div>
        <div>
          <label className="label">Sort</label>
          <select className="input" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="risk">risk</option><option value="channels">channels</option>
            <option value="posts">posts</option><option value="recent">last seen</option>
          </select>
        </div>
        <span className={cls('chip ml-auto', loading && 'text-accent')}>{loading ? 'loading…' : `${sorted.length} rows`}</span>
      </div>

      {sorted.length === 0 && !loading && <Empty>No identifiers match the filters.</Empty>}

      <div className="card overflow-hidden">
        <table className="w-full">
          <thead>
            <tr>
              {['Type', 'Value', 'Posts', 'Channels', 'Categories', 'First seen', 'Last seen', 'Risk', 'Alerts'].map((h) => <th key={h} className="th">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={`${r.type}:${r.value}`} className="hover:bg-raised/40">
                <td className="td"><span className="chip font-mono text-[10px]">{r.type}</span></td>
                <td className="td">
                  <Link className="link font-mono text-xs" to={`/graph?identifier=${encodeURIComponent(r.value)}`}>{r.value}</Link>
                  <div className="mt-0.5 flex gap-1">{(r.sources || []).map((s) => <SourceChip key={s} source={s} />)}</div>
                </td>
                <td className="td font-mono text-xs">{r.post_count}</td>
                <td className="td">
                  <span className={cls('font-mono text-xs', r.channel_count >= 3 && 'text-ok font-semibold')}>{r.channel_count}</span>
                  <div className="truncate text-[10px] text-muted" title={r.channels.join(', ')}>{r.channels.slice(0, 3).map((c) => `@${c}`).join(' ')}</div>
                </td>
                <td className="td">
                  <div className="flex flex-wrap gap-1">{r.categories.slice(0, 3).map((c) => <CategoryBadge key={c} category={c} size="xs" />)}</div>
                </td>
                <td className="td whitespace-nowrap font-mono text-[10px] text-muted">{fmtDate(r.first_seen)}</td>
                <td className="td whitespace-nowrap font-mono text-[10px] text-muted">{fmtDate(r.last_seen)}</td>
                <td className="td"><RiskPill score={r.risk_score} /></td>
                <td className="td font-mono text-xs">{r.alert_count}{r.campaign_count > 0 && <span className="ml-1 text-violet">·{r.campaign_count}c</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
