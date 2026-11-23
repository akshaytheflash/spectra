import React, { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { api, fmtDate } from '../lib/api';
import { LangToggle, useI18n } from '../lib/i18n';

const NAV = [
  { to: '/', key: 'nav.overview', icon: '◧' },
  { to: '/ingest', key: 'nav.ingest', icon: '⇪' },
  { to: '/alerts', key: 'nav.alerts', icon: '⚑' },
  { to: '/graph', key: 'nav.graph', icon: '◉' },
  { to: '/campaigns', key: 'nav.campaigns', icon: '◫' },
  { to: '/identifiers', key: 'nav.identifiers', icon: '⌗' },
  { to: '/map', key: 'nav.map', icon: '⌖' },
  { to: '/metrics', key: 'nav.metrics', icon: '▤' },
  { to: '/lexicon', key: 'nav.lexicon', icon: '✎' },
];

function NotificationBell() {
  const [items, setItems] = useState([]);
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState(() => Number(localStorage.getItem('spectra.seenAlerts') || 0));

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const r = await api.get('/alerts/recent');
        if (!cancelled) setItems(r.items || []);
      } catch { /* api offline */ }
    };
    load();
    const id = setInterval(load, 20000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Supabase Realtime (Priority-2): subscribe to alert inserts when configured.
  // Loaded from the app bundle (npm @supabase/supabase-js) to avoid remote code.
  useEffect(() => {
    let cleanup = () => {};
    (async () => {
      try {
        const cfg = await api.get('/realtime/config');
        if (!cfg.enabled) return;
        const { createClient } = await import('@supabase/supabase-js');
        const client = createClient(cfg.url, cfg.anon_key);
        const ch = client.channel(cfg.channel)
          .on('postgres_changes', { event: 'INSERT', schema: 'public', table: cfg.table }, (payload) => {
            setItems((cur) => [{ ...(payload.new || {}), live: true }, ...cur].slice(0, 15));
          })
          .subscribe();
        cleanup = () => client.removeChannel(ch);
      } catch { /* realtime optional */ }
    })();
    return () => cleanup();
  }, []);

  const unread = Math.max(0, items.length - seen);
  return (
    <div className="relative">
      <button className="btn relative px-2.5 py-1.5" onClick={() => { setOpen((o) => !o); if (!open) { setSeen(items.length); localStorage.setItem('spectra.seenAlerts', String(items.length)); } }}>
        <span>◔</span>
        {unread > 0 && <span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full bg-danger pulse-danger" />}
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-2 w-96 rounded-xl border border-line bg-panel shadow-2xl">
          <div className="flex items-center justify-between border-b border-line px-3 py-2 text-xs font-semibold uppercase tracking-wider text-muted">
            Recent alerts <span className="font-mono">{items.length}</span>
          </div>
          <ul className="max-h-96 overflow-y-auto">
            {items.length === 0 && <li className="p-4 text-sm text-muted">No alerts yet.</li>}
            {items.map((a) => (
              <li key={a.id} className="border-b border-line/60 px-3 py-2 last:border-0 hover:bg-raised/50">
                <a href={`/alerts?focus=${a.id}`} className="block">
                  <div className="flex items-center gap-2 text-[11px]">
                    <span className="chip font-mono text-[10px]">{a.category}</span>
                    <span className="text-muted">@{a.channel}</span>
                    <span className="ml-auto text-muted">{fmtDate(a.created_at)}</span>
                  </div>
                  <div className="mt-1 line-clamp-2 text-xs text-ink/90">{a.text}</div>
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function Layout() {
  const { t } = useI18n();
  const loc = useLocation();
  return (
    <div className="flex min-h-full">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-line bg-panel/60 lg:flex">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <div className="grid h-9 w-9 place-items-center rounded-lg border border-accent/40 bg-accent/10 font-mono text-lg font-bold text-accent">S</div>
          <div>
            <div className="text-sm font-bold tracking-[0.18em] text-ink">{t('app.name')}</div>
            <div className="text-[10px] uppercase tracking-wider text-muted">OSINT · v1.0</div>
          </div>
        </div>
        <nav className="mt-1 flex-1 space-y-0.5 px-2">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'}
              className={({ isActive }) => `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${isActive ? 'bg-accent/10 text-accent' : 'text-muted hover:bg-raised/60 hover:text-ink'}`}>
              <span className="w-4 text-center font-mono text-xs opacity-80">{n.icon}</span>
              {t(n.key)}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-line px-4 py-3 text-[10px] leading-relaxed text-muted">
          <div className="flex items-center gap-1.5"><span className="h-1.5 w-1.5 rounded-full bg-ok" /> Public channels only</div>
          <div className="mt-1 font-mono">read-only collector</div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-line bg-base/80 px-4 py-2.5 backdrop-blur lg:px-6">
          <div className="lg:hidden font-bold tracking-[0.18em] text-accent">SPECTRA</div>
          <div className="hidden text-xs text-muted sm:block">{t('app.tagline')}</div>
          <div className="ml-auto flex items-center gap-2">
            <SystemStatus />
            <NotificationBell />
            <LangToggle />
          </div>
        </header>
        <main key={loc.pathname} className="min-w-0 flex-1 p-4 lg:p-6">
          <div className="lg:hidden mb-4 flex gap-1 overflow-x-auto pb-1">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.to === '/'}
                className={({ isActive }) => `whitespace-nowrap rounded-md px-2.5 py-1 text-xs ${isActive ? 'bg-accent/15 text-accent' : 'text-muted'}`}>
                {t(n.key)}
              </NavLink>
            ))}
          </div>
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function SystemStatus() {
  const [health, setHealth] = useState(null);
  useEffect(() => { api.get('/health').then(setHealth).catch(() => setHealth(null)); }, []);
  if (!health) return <span className="chip text-[10px] text-danger">API offline</span>;
  return (
    <span className="hidden items-center gap-2 md:flex">
      <span className="chip text-[10px] font-mono">{health.model}</span>
      <span className="chip text-[10px] font-mono">{health.database}</span>
    </span>
  );
}
