import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { api, CATEGORY_COLORS, CATEGORY_LABELS, fmtDay } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { CategoryBadge, Confidence, DateCell, Kpi, RiskBadge, StatusBadge } from '../components/ui';

const CATS = ['drugs', 'arms', 'fake_docs', 'mule_account', 'atm_card', 'sim_card', 'net_banking_kit'];

function ChartCard({ title, children, right }) {
  return (
    <section className="card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">{title}</h3>
        {right}
      </div>
      <div className="h-64">{children}</div>
    </section>
  );
}

const tooltipStyle = {
  background: '#111a2c', border: '1px solid #1c2940', borderRadius: 8, color: '#e6edf7', fontSize: 12,
};

export default function Overview() {
  const { t } = useI18n();
  const [stats, setStats] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [campaigns, setCampaigns] = useState([]);
  const [err, setErr] = useState(null);

  useEffect(() => {
    Promise.all([api.get('/stats'), api.get('/alerts?page_size=8&sort=risk&order=desc'), api.get('/campaigns')])
      .then(([s, a, c]) => { setStats(s); setAlerts(a.items); setCampaigns(c.items.slice(0, 4)); })
      .catch((e) => setErr(e.message));
  }, []);

  if (err) return <div className="card p-6 text-sm text-danger">API error: {err}</div>;
  if (!stats) return <div className="text-sm text-muted">{t('common.loading')}</div>;

  const series = (stats.alerts_over_time || []).map((d) => ({
    date: fmtDay(d.date), ...Object.fromEntries(CATS.map((c) => [c, d[c] || 0])),
  }));
  const pie = Object.entries(stats.alerts_by_category || {})
    .filter(([, v]) => v > 0)
    .map(([k, v]) => ({ name: CATEGORY_LABELS[k] || k, key: k, value: v }));
  const sources = Object.entries(stats.source_breakdown || {}).map(([k, v]) => ({
    name: { text: 'Text', image_ocr: 'Image OCR', qr: 'QR code', voice: 'Voice' }[k] || k, value: v, key: k,
  }));

  return (
    <div className="space-y-5">
      <PageHead title={t('nav.overview')} sub="Live picture of public-channel monitoring" />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <Kpi label={t('kpi.posts')} value={stats.posts_scanned.toLocaleString()} sub={`${stats.channels_monitored} channels monitored`} />
        <Kpi label={t('kpi.alerts')} value={stats.alerts.toLocaleString()} accent="text-accent" sub={`${stats.alerts_by_status?.new || 0} new`} />
        <Kpi label={t('kpi.high')} value={stats.high_risk.toLocaleString()} accent="text-danger"
          sub={`${stats.risk_breakdown?.MEDIUM || 0} medium · ${stats.risk_breakdown?.LOW || 0} low`} />
        <Kpi label={t('kpi.identifiers')} value={stats.unique_identifiers.toLocaleString()} accent="text-ok" sub="phones · UPIs · wallets · handles" />
        <Kpi label={t('kpi.campaigns')} value={stats.active_campaigns.toLocaleString()} accent="text-violet" sub="coordinated clusters" />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <ChartCard title={t('chart.byCategory')}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={series} margin={{ top: 5, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke="#1c2940" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" tick={{ fill: '#8a9bb5', fontSize: 11 }} stroke="#1c2940" />
                <YAxis tick={{ fill: '#8a9bb5', fontSize: 11 }} stroke="#1c2940" allowDecimals={false} />
                <Tooltip contentStyle={tooltipStyle} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {CATS.map((c) => (
                  <Area key={c} type="monotone" dataKey={c} stackId="1" stroke={CATEGORY_COLORS[c]}
                    fill={CATEGORY_COLORS[c]} fillOpacity={0.28} name={CATEGORY_LABELS[c]} />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>
        <ChartCard title={t('chart.category')}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={pie} dataKey="value" nameKey="name" innerRadius={44} outerRadius={82} paddingAngle={2} stroke="#0c1322">
                {pie.map((p) => <Cell key={p.key} fill={CATEGORY_COLORS[p.key]} />)}
              </Pie>
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <ChartCard title={t('chart.topChannels')}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={stats.top_channels} layout="vertical" margin={{ top: 4, right: 16, left: 20, bottom: 0 }}>
                <CartesianGrid stroke="#1c2940" strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" tick={{ fill: '#8a9bb5', fontSize: 11 }} stroke="#1c2940" allowDecimals={false} />
                <YAxis type="category" dataKey="channel" width={150} tick={{ fill: '#8a9bb5', fontSize: 11 }} stroke="#1c2940" />
                <Tooltip contentStyle={tooltipStyle} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="posts" name="Posts" fill="#22d3ee" fillOpacity={0.65} radius={[0, 4, 4, 0]} />
                <Bar dataKey="alerts" name="Alerts" fill="#f43f5e" fillOpacity={0.85} radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>
        <ChartCard title={t('chart.sources')} right={<span className="font-mono text-[11px] text-muted">identifiers extracted</span>}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={sources} margin={{ top: 6, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid stroke="#1c2940" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="name" tick={{ fill: '#8a9bb5', fontSize: 11 }} stroke="#1c2940" />
              <YAxis tick={{ fill: '#8a9bb5', fontSize: 11 }} stroke="#1c2940" allowDecimals={false} />
              <Tooltip contentStyle={tooltipStyle} />
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                {sources.map((s) => <Cell key={s.key} fill={{ text: '#64748b', image_ocr: '#a78bfa', qr: '#22d3ee', voice: '#f472b6' }[s.key]} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <section className="card xl:col-span-2">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Highest-risk alerts</h3>
            <Link className="link text-xs" to="/alerts">View all →</Link>
          </div>
          <table className="w-full">
            <tbody>
              {alerts.map((a) => (
                <tr key={a.id} className="hover:bg-raised/40">
                  <td className="td w-6"><RiskBadge risk={a.risk} /></td>
                  <td className="td"><CategoryBadge category={a.category} /></td>
                  <td className="td max-w-0 truncate text-xs text-ink/90"><Link to={`/alerts?focus=${a.id}`}>{a.text}</Link></td>
                  <td className="td whitespace-nowrap text-[11px] text-muted">@{a.channel}</td>
                  <td className="td"><Confidence value={a.confidence} /></td>
                  <td className="td"><StatusBadge status={a.status} /></td>
                  <td className="td"><DateCell value={a.timestamp} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="card">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Campaigns</h3>
            <Link className="link text-xs" to="/campaigns">All →</Link>
          </div>
          <ul className="divide-y divide-line/60">
            {campaigns.map((c) => (
              <li key={c.id} className="px-4 py-3">
                <Link to={`/campaigns?focus=${c.id}`} className="text-sm font-medium text-ink hover:text-accent">{c.title}</Link>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <CategoryBadge category={c.category} />
                  <span className="chip font-mono text-[10px]">{c.channels.length} channels</span>
                  <span className="chip font-mono text-[10px]">{Math.round(c.similarity * 100)}% match</span>
                  <span className="chip font-mono text-[10px]">{c.posting_frequency.toFixed(1)}/day</span>
                </div>
                <div className="mt-1.5 truncate font-mono text-[10px] text-muted">{c.shared_identifiers.slice(0, 3).join(' · ')}</div>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}

export function PageHead({ title, sub, right }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
      </div>
      {right && <div className="flex flex-wrap items-center gap-2">{right}</div>}
    </div>
  );
}
