import React, { useCallback, useEffect, useState } from 'react';
import { api, cls, timeAgo } from '../lib/api';
import { PageHead } from './Overview';
import { Empty, RiskBadge } from '../components/ui';

function StatusCell({ label, value, ok, detail }) {
  return (
    <div className="px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium text-muted">{label}</span>
        <span className={cls('h-1.5 w-1.5 rounded-full', ok ? 'bg-accent live-dot' : 'bg-line')} />
      </div>
      <div className="mt-1 truncate text-[13px] font-medium text-ink">{value}</div>
      {detail && <div className="mt-0.5 truncate text-[10px] text-muted">{detail}</div>}
    </div>
  );
}

function JoinForm({ onDone }) {
  const [target, setTarget] = useState('');
  const [monitor, setMonitor] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    if (!target.trim()) return;
    setBusy(true); setMsg(null);
    try {
      const r = await api.post('/channels/join', { target: target.trim(), monitor });
      setMsg({ ok: r.ok, text: r.ok ? `Joined @${r.channel.username} and added it to the watchlist.` : r.reason });
      if (r.ok) { setTarget(''); onDone?.(); }
    } catch (err) {
      setMsg({ ok: false, text: err.message });
    } finally { setBusy(false); }
  };

  return (
    <form onSubmit={submit} className="card p-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <div className="min-w-0 flex-1">
          <label className="label" htmlFor="join-target">Join a public channel</label>
          <input id="join-target" className="input" value={target} onChange={(e) => setTarget(e.target.value)}
            placeholder="@channel, t.me/channel or t.me/channel/123" />
        </div>
        <label className="flex items-center gap-2 text-[12px] text-muted sm:mt-4">
          <input type="checkbox" checked={monitor} onChange={(e) => setMonitor(e.target.checked)} />
          add to watchlist
        </label>
        <button className="btn btn-primary sm:mt-4" disabled={busy || !target.trim()}>
          {busy ? 'Joining…' : 'Join & watch'}
        </button>
      </div>
      {msg && <p className={cls('mt-2 text-[11px]', msg.ok ? 'text-accent' : 'text-danger')}>{msg.text}</p>}
    </form>
  );
}

function DiscoverPanel({ onJoined }) {
  const [q, setQ] = useState('');
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [bulk, setBulk] = useState(false);

  const search = async (e) => {
    e?.preventDefault();
    if (!q.trim()) return;
    setBusy(true); setMsg(null);
    try {
      const r = await api.get(`/channels/discover?q=${encodeURIComponent(q.trim())}&limit=20`);
      setItems(r.items || []);
      if (!r.ok) setMsg({ ok: false, text: r.reason || 'Telegram session not configured — discovery needs a user session.' });
    } catch (err) { setMsg({ ok: false, text: err.message }); }
    finally { setBusy(false); }
  };

  const join = async (username) => {
    setBusy(true);
    try {
      const r = await api.post('/channels/join', { target: username, monitor: true });
      setItems((cur) => cur.map((c) => c.username === username ? { ...c, known: true, joined: r.ok } : c));
      setMsg({ ok: r.ok, text: r.ok ? `Joined @${username}.` : r.reason });
      if (r.ok) onJoined?.();
    } catch (err) { setMsg({ ok: false, text: err.message }); }
    finally { setBusy(false); }
  };

  const autoJoin = async () => {
    setBulk(true); setMsg(null);
    try {
      const r = await api.post('/channels/discover', { join: true, per_keyword: 2 });
      setMsg({ ok: true, text: `Joined ${r.joined?.length || 0} channel(s) from the watch keywords.` });
      onJoined?.();
    } catch (err) { setMsg({ ok: false, text: err.message }); }
    finally { setBulk(false); }
  };

  return (
    <div className="space-y-3">
      <form className="card flex flex-col gap-2 p-3 sm:flex-row" onSubmit={search}>
        <input className="input flex-1" value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Search public Telegram channels by keyword — e.g. “ganja”, “katta”, “mule account”" />
        <button className="btn" disabled={busy || !q.trim()}>{busy ? 'Searching…' : 'Search'}</button>
        <button type="button" className="btn" onClick={autoJoin} disabled={bulk}>
          {bulk ? 'Joining…' : 'Auto-discover & join'}
        </button>
      </form>
      {msg && <p className={cls('text-[11px]', msg.ok ? 'text-accent' : 'text-danger')}>{msg.text}</p>}

      {items.length === 0 ? (
        <Empty>Search to preview public channels. Joining requires a Telethon user session.</Empty>
      ) : (
        <div className="card overflow-hidden">
          <table className="w-full">
            <thead><tr>
              <th className="th">Channel</th><th className="th">Subscribers</th><th className="th hidden md:table-cell">About</th><th className="th text-right">Action</th>
            </tr></thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.username} className="hover:bg-raised/40">
                  <td className="td">
                    <div className="text-[12.5px] font-medium text-ink">@{c.username}</div>
                    <div className="text-[11px] text-muted">{c.title}</div>
                  </td>
                  <td className="td font-mono text-[11px] text-muted">{c.subscribers?.toLocaleString?.() || c.subscribers || '—'}</td>
                  <td className="td hidden max-w-[30rem] md:table-cell"><div className="line-clamp-2 text-[11px] text-muted">{c.about || '—'}</div></td>
                  <td className="td text-right">
                    {c.joined || c.known ? (
                      <span className="chip text-accent">in watchlist</span>
                    ) : (
                      <button className="btn px-2 py-1 text-[11px]" disabled={busy} onClick={() => join(c.username)}>Join</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function Channels() {
  const [tab, setTab] = useState('watchlist');
  const [channels, setChannels] = useState([]);
  const [status, setStatus] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [msg, setMsg] = useState(null);
  const [job, setJob] = useState(null);

  const load = useCallback(async () => {
    const [c, s] = await Promise.all([api.get('/channels?limit=500'), api.get('/channels/status')]);
    setChannels(c.items || []); setStatus(s);
  }, []);

  useEffect(() => { load().catch((e) => setMsg({ ok: false, text: e.message })); }, [load]);

  const patchMonitor = async (ch) => {
    setBusyId(ch.id);
    try {
      await api.post(`/channels/${ch.id}/monitor`, { enabled: !ch.is_monitored });
      await load();
    } catch (e) { setMsg({ ok: false, text: e.message }); } finally { setBusyId(null); }
  };

  const scan = async (ch) => {
    setBusyId(ch.id); setMsg(null); setJob({ channel: ch.username, status: 'running', done: 0, total: 50 });
    try {
      const r = await api.post(`/channels/${ch.id}/scan?limit=50`, {});
      setJob({ ...r, channel: ch.username });
      pollJob(r.id);
    } catch (e) {
      setMsg({ ok: false, text: e.message }); setBusyId(null); setJob(null);
    }
  };

  const pollJob = (id) => {
    let tries = 0;
    const tick = async () => {
      try {
        const j = await api.get(`/ingest/jobs/${id}`);
        setJob((cur) => ({ ...(cur || {}), ...j }));
        if (j.status === 'running' && tries < 300) { tries += 1; setTimeout(tick, 2500); }
        else { setBusyId(null); await load(); }
      } catch { setBusyId(null); }
    };
    setTimeout(tick, 1800);
  };

  const leave = async (ch) => {
    if (!window.confirm(`Leave @${ch.username} and remove it from the watchlist?`)) return;
    setBusyId(ch.id);
    try {
      await api.del(`/channels/${ch.id}/join`);
      await load();
    } catch (e) { setMsg({ ok: false, text: e.message }); } finally { setBusyId(null); }
  };

  const sweep = async () => {
    setMsg(null);
    try {
      const r = await api.post('/ops/sweep', {});
      setMsg({ ok: !!r.ok, text: r.ok ? `Sweep complete: ${r.messages} messages, ${r.alerts} alerts.` : r.reason });
      await load();
    } catch (e) { setMsg({ ok: false, text: e.message }); }
  };

  const monitored = channels.filter((c) => c.is_monitored);
  const candidates = channels.filter((c) => !c.is_monitored);

  return (
    <div className="space-y-4">
      <PageHead
        title="Channels"
        sub="Discover, join and supervise public Telegram channels. Collection is public-only and evidence is hashed."
        right={(
          <div className="flex items-center gap-2">
            <button className="btn" onClick={sweep}>Run sweep</button>
            <button className="btn btn-primary" onClick={() => setTab(tab === 'discover' ? 'watchlist' : 'discover')}>
              {tab === 'discover' ? 'View watchlist' : 'Discover channels'}
            </button>
          </div>
        )}
      />

      <div className="card grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
        <div className="border-b border-line sm:border-b-0 sm:border-r">
          <StatusCell label="Telegram session" ok={status?.telegram_session}
            value={status?.telegram_session ? 'Connected' : 'Not configured'}
            detail={status?.telegram_session ? 'Telethon user session' : 'Demo mode · add API id/hash + login'} />
        </div>
        <div className="border-b border-line lg:border-b-0 lg:border-r">
          <StatusCell label="Auto-pilot" ok={status?.autopilot?.running}
            value={status?.autopilot?.running ? 'Sweeping' : status?.autopilot?.enabled ? 'Armed (no session)' : 'Off'}
            detail={`every ${Math.round((status?.autopilot?.interval_seconds || 0) / 60)} min`} />
        </div>
        <div className="border-b border-line sm:border-b-0 sm:border-r">
          <StatusCell label="Analyst bot" ok={status?.telegram_bot?.running || status?.telegram_bot?.enabled}
            value={status?.telegram_bot?.enabled ? 'Token configured' : 'No token'}
            detail={`${status?.telegram_bot?.subscribers || 0} subscriber(s)`} />
        </div>
        <StatusCell label="Watchlist" ok={monitored.length > 0} value={`${monitored.length} monitoring`}
          detail={`${channels.length} known channels`} />
      </div>

      <JoinForm onDone={load} />

      {msg && <div className={cls('border px-3 py-2 text-[12px]', msg.ok ? 'border-accent/30 bg-accent/5 text-accent' : 'border-danger/30 bg-danger/5 text-danger')}>{msg.text}</div>}
      {job && (
        <div className="border border-line bg-panel px-3 py-2 text-[12px]">
          <div className="flex items-center justify-between">
            <span>Scanning @{job.channel} · {job.done || 0}/{job.total || 0}</span>
            <span className="font-mono text-[10px] text-muted">{job.status}</span>
          </div>
          <div className="mt-1.5 h-1 overflow-hidden rounded bg-line">
            <div className="h-full bg-accent transition-all" style={{ width: `${job.total ? Math.min(100, (job.done / job.total) * 100) : 0}%` }} />
          </div>
          {job.message && <div className="mt-1 truncate text-[10px] text-muted">{job.message}</div>}
        </div>
      )}

      <div className="flex items-center gap-3 border-b border-line">
        <button className={cls('border-b-2 px-1 pb-2 text-[12px] font-medium', tab === 'watchlist' ? 'border-accent text-ink' : 'border-transparent text-muted')} onClick={() => setTab('watchlist')}>
          Watchlist ({monitored.length})
        </button>
        <button className={cls('border-b-2 px-1 pb-2 text-[12px] font-medium', tab === 'discover' ? 'border-accent text-ink' : 'border-transparent text-muted')} onClick={() => setTab('discover')}>
          Discover
        </button>
        <button className={cls('border-b-2 px-1 pb-2 text-[12px] font-medium', tab === 'other' ? 'border-accent text-ink' : 'border-transparent text-muted')} onClick={() => setTab('other')}>
          Other known channels ({candidates.length})
        </button>
      </div>

      {tab === 'discover' ? <DiscoverPanel onJoined={load} /> : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead><tr>
                <th className="th">Channel</th>
                <th className="th">Join</th>
                <th className="th hidden sm:table-cell">Subscribers</th>
                <th className="th">Posts</th>
                <th className="th">Alerts</th>
                <th className="th hidden md:table-cell">Last scan</th>
                <th className="th text-right">Actions</th>
              </tr></thead>
              <tbody>
                {(tab === 'watchlist' ? monitored : candidates).map((c) => (
                  <tr key={c.id} className="hover:bg-raised/40">
                    <td className="td">
                      <div className="flex items-center gap-2">
                        <span className={cls('h-1.5 w-1.5 rounded-full', c.is_monitored ? 'bg-accent' : 'bg-line')} />
                        <div className="min-w-0">
                          <div className="truncate text-[12.5px] font-medium text-ink">@{c.username}</div>
                          <div className="truncate text-[10px] text-muted">{c.title || '—'}</div>
                        </div>
                      </div>
                    </td>
                    <td className="td"><span className="chip">{c.join_status}</span></td>
                    <td className="td hidden font-mono text-[11px] text-muted sm:table-cell">{c.subscriber_count?.toLocaleString?.() || c.subscriber_count || '—'}</td>
                    <td className="td font-mono text-[11px]">{c.posts}</td>
                    <td className="td font-mono text-[11px]">{c.alerts}</td>
                    <td className="td hidden whitespace-nowrap text-[11px] text-muted md:table-cell">{c.last_scraped ? timeAgo(c.last_scraped) : 'never'}</td>
                    <td className="td">
                      <div className="flex items-center justify-end gap-1">
                        <button className="btn px-2 py-1 text-[11px]" disabled={busyId === c.id}
                          onClick={() => scan(c)}>{busyId === c.id ? 'Scanning…' : 'Scan'}</button>
                        <button className="btn px-2 py-1 text-[11px]" disabled={busyId === c.id} onClick={() => patchMonitor(c)}>
                          {c.is_monitored ? 'Pause' : 'Monitor'}
                        </button>
                        <button className="btn btn-quiet px-2 py-1 text-[11px] text-danger" disabled={busyId === c.id} onClick={() => leave(c)}>Leave</button>
                      </div>
                    </td>
                  </tr>
                ))}
                {(tab === 'watchlist' ? monitored : candidates).length === 0 && (
                  <tr><td className="td px-4 py-8 text-[13px] text-muted" colSpan={7}>
                    {tab === 'watchlist' ? 'No channels under watch. Join one above.' : 'No other channels recorded.'}
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
