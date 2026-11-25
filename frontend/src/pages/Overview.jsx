import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  api, CATEGORY_COLORS, CATEGORY_LABELS, cls, fmtDay, timeAgo,
} from '../lib/api';
import { useI18n } from '../lib/i18n';
import {
  CategoryBadge, Confidence, EmojiSignals, FlagSource, Kpi, RiskBadge,
} from '../components/ui';

const CATS = ['drugs', 'arms', 'fake_docs', 'mule_account', 'atm_card', 'sim_card', 'net_banking_kit'];

const tooltipStyle = {
  background: 'rgb(var(--c-panel))',
  border: '1px solid rgb(var(--c-line))',
  borderRadius: 6,
  color: 'rgb(var(--c-ink))',
  fontSize: 11,
};

function SignalBand({ stats, status }) {
  const series = stats.alerts_over_time || [];
  const recent = series.slice(-14);
  const max = Math.max(1, ...recent.map((d) => CATS.reduce((n, c) => n + (d[c] || 0), 0)));
  const total = recent.reduce((n, d) => n + CATS.reduce((m, c) => m + (d[c] || 0), 0), 0);
  return (
    <section className="card grid grid-cols-1 lg:grid-cols-[1.4fr_1fr_1fr_1fr]">
      <div className="border-b border-line px-4 py-3 lg:border-b-0 lg:border-r">
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-medium text-muted">Alert volume · last 14 days</span>
          <span className="flex items-center gap-1.5 text-[10px] text-muted">
            <span className={cls('h-1.5 w-1.5 rounded-full', status?.telegram_session ? 'bg-accent live-dot' : 'bg-line')} />
            {status?.autopilot?.running ? 'Auto-pilot on' : status?.telegram_session ? 'Manual sweeps' : 'Demo data'}
          </span>
        </div>
        <div className="mt-2 flex items-end gap-3">
          <span className="font-mono text-[30px] font-medium leading-none tracking-tight text-ink">{total.toLocaleString()}</span>
          <span className="pb-1 text-[11px] text-muted">alerts</span>
        </div>
        <div className="mt-3 flex h-9 items-end gap-[3px]" aria-hidden="true">
          {recent.map((d) => {
            const n = CATS.reduce((m, c) => m + (d[c] || 0), 0);
            return (
              <div key={d.date} className="group relative flex-1">
                <div
                  className="w-full rounded-[1px] signal-bar"
                  style={{ height: `${Math.max(2, (n / max) * 36)}px` }}
                  data-hot={n >= max * 0.7}
                  title={`${fmtDay(d.date)} · ${n} alerts`}
                />
              </div>
            );
          })}
          {recent.length === 0 && <div className="text-[11px] text-muted">No alerts yet.</div>}
        </div>
      </div>
      <div className="border-b border-line lg:border-b-0 lg:border-r"><Kpi label="Messages scanned" value={(stats.posts_scanned || 0).toLocaleString()} sub={`${stats.channels_monitored || 0} public channels`} /></div>
      <div className="border-b border-line lg:border-b-0 lg:border-r"><Kpi label="High risk" value={(stats.high_risk || 0).toLocaleString()} accent="text-danger" sub={`${stats.risk_breakdown?.MEDIUM || 0} medium · ${stats.risk_breakdown?.LOW || 0} low`} /></div>
      <div><Kpi label="Identifiers" value={(stats.unique_identifiers || 0).toLocaleString()} sub="phones · UPI · wallets" /></div>
    </section>
  );
}

function PriorityQueue({ alerts }) {
  return (
    <section className="card min-w-0">
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <div className="flex items-baseline gap-2">
          <h3 className="section-title">Priority queue</h3>
          <span className="font-mono text-[11px] text-muted">{alerts.length}</span>
        </div>
        <Link className="text-[11px] font-medium text-accent hover:underline" to="/alerts">Open alerts</Link>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr>
              <th className="th pl-3">Risk</th>
              <th className="th">Category</th>
              <th className="th">Message</th>
              <th className="th hidden md:table-cell">Channel</th>
              <th className="th hidden sm:table-cell">Flag</th>
              <th className="th text-right">When</th>
            </tr>
          </thead>
          <tbody>
            {alerts.map((a) => (
              <tr key={a.id} className="group hover:bg-raised/40">
                <td className={cls('td w-0 whitespace-nowrap pl-3 risk-spine', `risk-${a.risk}`)}><RiskBadge risk={a.risk} /></td>
                <td className="td whitespace-nowrap"><CategoryBadge category={a.category} size="xs" /></td>
                <td className="td max-w-0">
                  <Link to={`/alerts?focus=${a.id}`} className="block truncate text-[12.5px] text-ink group-hover:text-accent">
                    {(a.emoji || []).slice(0, 3).map((e) => e.emoji).join(' ')} {a.text}
                  </Link>
                  <div className="mt-1 flex items-center gap-2">
                    {a.llm_flag && <span className="text-[10px] font-medium text-violet">GLM confirmed</span>}
                    {a.emoji?.length > 0 && <span className="text-[10px] text-muted">{a.emoji.length} emoji signal(s)</span>}
                  </div>
                </td>
                <td className="td hidden whitespace-nowrap font-mono text-[11px] text-muted md:table-cell">@{a.channel}</td>
                <td className="td hidden sm:table-cell"><FlagSource source={a.flag_source} /></td>
                <td className="td whitespace-nowrap text-right text-[11px] text-muted">{timeAgo(a.timestamp || a.created_at)}</td>
              </tr>
            ))}
            {alerts.length === 0 && (
              <tr><td className="td px-4 py-8 text-[13px] text-muted" colSpan={6}>No alerts yet. Join a channel and run a scan.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Watchlist({ channels, status }) {
  return (
    <section className="card">
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <h3 className="section-title">Channels under watch</h3>
        <Link className="text-[11px] font-medium text-accent hover:underline" to="/channels">Manage</Link>
      </div>
      <ul className="divide-y divide-line/70">
        {channels.map((c) => (
          <li key={c.id} className="flex items-center gap-2 px-4 py-2">
            <span className={cls('h-1.5 w-1.5 shrink-0 rounded-full', c.is_monitored ? 'bg-accent' : 'bg-line')} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[12px] font-medium text-ink">@{c.username}</div>
              <div className="truncate text-[10px] text-muted">{c.title || c.join_status} · {c.posts} posts</div>
            </div>
            <span className="font-mono text-[10px] text-muted">{c.alerts}</span>
          </li>
        ))}
        {channels.length === 0 && <li className="px-4 py-6 text-[12px] text-muted">No channels yet.</li>}
      </ul>
      <div className="border-t border-line px-4 py-2 text-[10px] text-muted">
        {status?.telegram_session ? 'Telethon session active' : 'Demo mode — add a Telegram session to go live'}
      </div>
    </section>
  );
}

function DetectionPanel({ stats }) {
  const llm = stats.llm || {};
  return (
    <section className="card">
      <div className="border-b border-line px-4 py-2.5"><h3 className="section-title">Detection read-out</h3></div>
      <div className="px-4 py-3">
        <div className="flex items-center justify-between">
          <span className="text-[12px] font-medium text-ink">GLM message scanner</span>
          <span className={cls('chip', llm.configured ? 'border-accent/40 text-accent' : 'text-muted')}>
            {llm.configured ? 'configured' : 'offline'}
          </span>
        </div>
        <div className="mt-2 space-y-0">
          <div className="stat-row"><span className="text-[11px] text-muted">Messages read by GLM</span><span className="font-mono text-[12px]">{(llm.scanned || 0).toLocaleString()}</span></div>
          <div className="stat-row"><span className="text-[11px] text-muted">GLM-confirmed flags</span><span className="font-mono text-[12px]">{llm.flagged || 0}</span></div>
          <div className="stat-row"><span className="text-[11px] text-muted">False positives cleared</span><span className="font-mono text-[12px]">{llm.vetoed || 0}</span></div>
        </div>
        {(stats.emoji_top || []).length > 0 && (
          <div className="mt-3">
            <div className="text-[10px] font-medium text-muted">Emoji signals on flagged posts</div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {stats.emoji_top.map((e) => (
                <span key={e.emoji} className="inline-flex items-center gap-1 rounded border border-line bg-raised/60 px-1.5 py-0.5">
                  <span className="text-[14px] leading-none">{e.emoji}</span>
                  <span className="font-mono text-[10px] text-muted">{e.count}</span>
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

export default function Overview() {
  const { t } = useI18n();
  const [stats, setStats] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [campaigns, setCampaigns] = useState([]);
  const [channels, setChannels] = useState([]);
  const [status, setStatus] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    Promise.all([
      api.get('/stats'),
      api.get('/alerts?page_size=9&sort=timestamp&order=desc'),
      api.get('/campaigns'),
      api.get('/channels?monitored=true&limit=6'),
      api.get('/channels/status'),
    ])
      .then(([s, a, c, ch, st]) => {
        setStats(s); setAlerts(a.items || []); setCampaigns((c.items || []).slice(0, 4));
        setChannels(ch.items || []); setStatus(st);
      })
      .catch((e) => setErr(e.message));
  }, []);

  const series = useMemo(() => (stats?.alerts_over_time || []).map((d) => ({
    date: fmtDay(d.date), ...Object.fromEntries(CATS.map((c) => [c, d[c] || 0])),
  })), [stats]);

  if (err) return <div className="card p-5 text-[13px] text-danger">API error: {err}</div>;
  if (!stats) return <div className="py-10 text-[13px] text-muted">{t('common.loading')}</div>;

  return (
    <div className="space-y-4">
      <PageHead
        title="Watchfloor"
        sub="Public Telegram channels under monitoring · every flagged message is evidence-hashed"
        right={(
          <div className="flex items-center gap-2">
            <span className="hidden items-center gap-1.5 text-[11px] text-muted sm:flex">
              <span className="h-1.5 w-1.5 rounded-full bg-accent live-dot" />
              {new Date().toLocaleDateString(undefined, { weekday: 'short', day: '2-digit', month: 'short' })}
            </span>
            <Link to="/channels" className="btn btn-primary">Scan a channel</Link>
          </div>
        )}
      />

      <SignalBand stats={stats} status={status} />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-4">
          <PriorityQueue alerts={alerts} />

          <div className="grid gap-4 lg:grid-cols-[1.5fr_1fr]">
            <section className="card">
              <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
                <h3 className="section-title">Alerts by category</h3>
                <span className="text-[10px] text-muted">stacked, per day</span>
              </div>
              <div className="h-56 px-2 py-3">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={series} margin={{ top: 4, right: 8, left: -22, bottom: 0 }}>
                    <CartesianGrid stroke="rgb(var(--c-line))" strokeDasharray="2 4" vertical={false} />
                    <XAxis dataKey="date" tick={{ fill: 'rgb(var(--c-muted))', fontSize: 10 }} stroke="rgb(var(--c-line))" tickLine={false} />
                    <YAxis tick={{ fill: 'rgb(var(--c-muted))', fontSize: 10 }} stroke="rgb(var(--c-line))" tickLine={false} allowDecimals={false} />
                    <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: 'rgb(var(--c-muted))' }} />
                    {CATS.map((c) => (
                      <Area key={c} type="monotone" dataKey={c} stackId="1"
                        stroke={CATEGORY_COLORS[c]} fill={CATEGORY_COLORS[c]} fillOpacity={0.32}
                        name={CATEGORY_LABELS[c]} strokeWidth={1.4} />
                    ))}
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </section>

            <section className="card">
              <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
                <h3 className="section-title">Top channels</h3>
                <span className="text-[10px] text-muted">posts / alerts</span>
              </div>
              <div className="h-56 px-2 py-3">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={stats.top_channels || []} layout="vertical" margin={{ top: 2, right: 10, left: 4, bottom: 0 }}>
                    <CartesianGrid stroke="rgb(var(--c-line))" strokeDasharray="2 4" horizontal={false} />
                    <XAxis type="number" tick={{ fill: 'rgb(var(--c-muted))', fontSize: 10 }} stroke="rgb(var(--c-line))" tickLine={false} allowDecimals={false} />
                    <YAxis type="category" dataKey="channel" width={104} tick={{ fill: 'rgb(var(--c-muted))', fontSize: 10 }} stroke="rgb(var(--c-line))" tickLine={false} />
                    <Tooltip contentStyle={tooltipStyle} />
                    <Bar dataKey="posts" name="Posts" fill={CATEGORY_COLORS.mule_account} fillOpacity={0.5} radius={[0, 2, 2, 0]} />
                    <Bar dataKey="alerts" name="Alerts" fill={CATEGORY_COLORS.arms} fillOpacity={0.85} radius={[0, 2, 2, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>
          </div>
        </div>

        <div className="space-y-4">
          <Watchlist channels={channels} status={status} />
          <DetectionPanel stats={stats} />
          <section className="card">
            <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
              <h3 className="section-title">Coordinated campaigns</h3>
              <Link className="text-[11px] font-medium text-accent hover:underline" to="/campaigns">All</Link>
            </div>
            <ul className="divide-y divide-line/70">
              {campaigns.map((c) => (
                <li key={c.id} className="px-4 py-2.5">
                  <Link to={`/campaigns?focus=${c.id}`} className="block truncate text-[12px] font-medium text-ink hover:text-accent">{c.title}</Link>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-muted">
                    <span>{c.channels.length} channels</span>
                    <span>·</span>
                    <span>{Math.round(c.similarity * 100)}% match</span>
                    <span>·</span>
                    <span>{c.posting_frequency.toFixed(1)}/day</span>
                  </div>
                </li>
              ))}
              {campaigns.length === 0 && <li className="px-4 py-6 text-[12px] text-muted">No coordinated clusters yet.</li>}
            </ul>
          </section>
        </div>
      </div>
    </div>
  );
}

export function PageHead({ title, sub, right }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-[22px] font-semibold leading-tight tracking-tight text-ink">{title}</h1>
        {sub && <p className="mt-0.5 text-[12px] text-muted">{sub}</p>}
      </div>
      {right && <div className="flex flex-wrap items-center gap-2">{right}</div>}
    </div>
  );
}
