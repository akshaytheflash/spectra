import React, { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, cls, fmtDate } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { CategoryBadge, Drawer, Empty, SourceChip } from '../components/ui';
import IdentityProfile from '../components/IdentityProfile';
import { PageHead } from './Overview';

const TYPES = ['', 'phone', 'upi', 'wallet', 'username', 'location', 'bank', 'ifsc', 'link', 'email'];

function RiskPill({ score }) {
  const c = score >= 60 ? 'rgb(var(--c-danger))' : score >= 30 ? 'rgb(var(--c-warn))' : 'rgb(var(--c-accent))';
  return (
    <div className="flex items-center gap-2">
      <span className="h-1 w-16 overflow-hidden rounded-full bg-line">
        <span className="block h-full rounded-full" style={{ width: `${Math.min(100, score)}%`, background: c }} />
      </span>
      <span className="font-mono text-[11px]" style={{ color: c }}>{score.toFixed(1)}</span>
    </div>
  );
}

export default function Identifiers() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState(params.get('q') || '');
  const [type, setType] = useState('');
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState('risk');
  const [focus, setFocus] = useState(params.get('focus'));

  useEffect(() => {
    setLoading(true);
    const qs = new URLSearchParams();
    if (q) qs.set('q', q);
    if (type) qs.set('type', type);
    api.get(`/identifiers?${qs}`).then((r) => setRows(r.items || [])).finally(() => setLoading(false));
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

  const open = (value) => { setFocus(value); setParams({ focus: value }); };

  return (
    <div className="space-y-4">
      <PageHead
        title={t('identifiers.title')}
        sub={`${rows.length} identifiers normalised across channels — phones, UPI IDs, wallets and handles`}
        right={<a className="btn" href={api.rawUrl('/identifiers/export.csv')} download>Export CSV</a>}
      />

      <div className="card flex flex-wrap items-end gap-2 p-3">
        <div className="min-w-[240px] flex-1">
          <label className="label">{t('common.search')}</label>
          <input className="input" placeholder="phone, UPI, wallet, @handle, city (fuzzy)" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div>
          <label className="label">Type</label>
          <select className="input" value={type} onChange={(e) => setType(e.target.value)}>
            {TYPES.map((x) => <option key={x} value={x}>{x || 'All types'}</option>)}
          </select>
        </div>
        <div>
          <label className="label">Sort</label>
          <select className="input" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="risk">Risk score</option><option value="channels">Channels</option>
            <option value="posts">Posts</option><option value="recent">Last seen</option>
          </select>
        </div>
        <span className={cls('ml-auto text-[11px]', loading ? 'text-accent' : 'text-muted')}>{loading ? 'Loading…' : `${sorted.length} rows`}</span>
      </div>

      {sorted.length === 0 && !loading && <Empty>No identifiers match the filters.</Empty>}

      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr>
                {['Type', 'Value', 'Posts', 'Channels', 'Categories', 'First seen', 'Last seen', 'Risk', 'Alerts'].map((h) => <th key={h} className="th">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => (
                <tr key={`${r.type}:${r.value}`} className="cursor-pointer hover:bg-raised/40" onClick={() => open(r.value)}>
                  <td className="td"><span className="chip font-mono">{r.type}</span></td>
                  <td className="td">
                    <button className="link font-mono text-[12px]" onClick={(e) => { e.stopPropagation(); open(r.value); }}>{r.value}</button>
                    <div className="mt-0.5 flex gap-1">{(r.sources || []).map((s) => <SourceChip key={s} source={s} />)}</div>
                  </td>
                  <td className="td font-mono text-[11px]">{r.post_count}</td>
                  <td className="td">
                    <span className={cls('font-mono text-[11px]', r.channel_count >= 3 && 'font-semibold text-accent')}>{r.channel_count}</span>
                    <div className="truncate text-[10px] text-muted" title={r.channels.join(', ')}>{r.channels.slice(0, 3).map((c) => `@${c}`).join(' ')}</div>
                  </td>
                  <td className="td"><div className="flex flex-wrap gap-1">{r.categories.slice(0, 3).map((c) => <CategoryBadge key={c} category={c} size="xs" />)}</div></td>
                  <td className="td whitespace-nowrap font-mono text-[10px] text-muted">{fmtDate(r.first_seen)}</td>
                  <td className="td whitespace-nowrap font-mono text-[10px] text-muted">{fmtDate(r.last_seen)}</td>
                  <td className="td"><RiskPill score={r.risk_score} /></td>
                  <td className="td font-mono text-[11px]">{r.alert_count}{r.campaign_count > 0 && <span className="ml-1 text-violet">·{r.campaign_count}c</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <Drawer open={!!focus} onClose={() => { setFocus(null); setParams({}); }} title="Identity profile" width="max-w-3xl">
        {focus && <IdentityProfile value={focus} />}
      </Drawer>
    </div>
  );
}
