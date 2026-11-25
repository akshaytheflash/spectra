import React, { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { api, cls, fmtTime } from '../lib/api';
import { LangToggle, useI18n } from '../lib/i18n';

const NAV = [
  {
    label: 'Watchfloor',
    items: [
      { to: '/', key: 'nav.overview', label: 'Watchfloor', glyph: 'M3 15h5l2-7 3 12 2-8 2 3h4' },
      { to: '/alerts', key: 'nav.alerts', glyph: 'M4 17V9a8 8 0 0 1 16 0v8l-3-2H7l-3 2Z' },
      { to: '/channels', key: 'nav.channels', label: 'Channels', glyph: 'M4 6h16M4 12h16M4 18h10' },
    ],
  },
  {
    label: 'Evidence',
    items: [
      { to: '/graph', key: 'nav.graph', glyph: 'M7 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm10 4a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM7 21a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm0-4 10-7M7 7v10' },
      { to: '/campaigns', key: 'nav.campaigns', glyph: 'M4 5h16v4H4zM4 15h10v4H4zM17 12h3v7h-3z' },
      { to: '/identifiers', key: 'nav.identifiers', glyph: 'M4 6h16M4 12h16M4 18h7' },
      { to: '/map', key: 'nav.map', glyph: 'M12 21s7-6.2 7-11a7 7 0 1 0-14 0c0 4.8 7 11 7 11Zm0-8a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z' },
    ],
  },
  {
    label: 'Desk',
    items: [
      { to: '/ingest', key: 'nav.ingest', glyph: 'M12 3v12m0 0 4-4m-4 4-4-4M4 17v3h16v-3' },
      { to: '/metrics', key: 'nav.metrics', glyph: 'M4 19V5m0 10 4-4 4 3 5-7 3 3' },
      { to: '/lexicon', key: 'nav.lexicon', glyph: 'M5 4h9l5 5v11H5zM14 4v5h5' },
    ],
  },
];

function Mark({ className = '' }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" fill="none">
      <rect x="1.5" y="1.5" width="21" height="21" rx="4" stroke="currentColor" strokeWidth="1.6" opacity="0.55" />
      <path d="M6 9h12M6 13h8M6 17h5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function useTheme() {
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme || 'light');
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('spectra.theme', theme);
  }, [theme]);
  return [theme, setTheme];
}

function NotificationBell() {
  const [items, setItems] = useState([]);
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState(() => Number(localStorage.getItem('spectra.seenAlerts') || 0));
  const navigate = useNavigate();

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

  const unread = Math.max(0, items.length - seen);
  return (
    <div className="relative">
      <button
        className="btn btn-quiet relative px-2 py-1.5"
        aria-label="Notifications"
        onClick={() => {
          setOpen((o) => !o);
          if (!open) { setSeen(items.length); localStorage.setItem('spectra.seenAlerts', String(items.length)); }
        }}
      >
        <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.7">
          <path d="M4 17V9a8 8 0 0 1 16 0v8l-3-2H7l-3 2Z" strokeLinejoin="round" />
        </svg>
        {unread > 0 && <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-danger" />}
      </button>
      {open && (
        <>
          <button className="fixed inset-0 z-30 cursor-default" aria-label="Close notifications" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-40 mt-1 w-[22rem] border border-line bg-panel shadow-2xl">
            <div className="flex items-center justify-between border-b border-line px-3 py-2">
              <span className="text-[11px] font-semibold text-ink">Recent alerts</span>
              <span className="font-mono text-[10px] text-muted">{items.length}</span>
            </div>
            <ul className="max-h-96 overflow-y-auto">
              {items.length === 0 && <li className="p-4 text-[13px] text-muted">No alerts yet.</li>}
              {items.map((a) => (
                <li key={a.id} className="border-b border-line/60 last:border-0 hover:bg-raised/50">
                  <button
                    className="block w-full px-3 py-2 text-left"
                    onClick={() => { setOpen(false); navigate(`/alerts?focus=${a.id}`); }}
                  >
                    <div className="flex items-center gap-2 text-[10px] text-muted">
                      <span className="font-medium text-ink">{a.category}</span>
                      <span>@{a.channel}</span>
                      <span className="ml-auto font-mono">{fmtTime(a.created_at)}</span>
                    </div>
                    <div className="mt-1 line-clamp-2 text-[12px] text-ink/90">{a.text}</div>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}

function GlobalSearch() {
  const [q, setQ] = useState('');
  const navigate = useNavigate();
  return (
    <form
      className="relative hidden min-w-0 flex-1 md:block md:max-w-md"
      onSubmit={(e) => { e.preventDefault(); if (q.trim()) navigate(`/alerts?q=${encodeURIComponent(q.trim())}`); }}
    >
      <svg viewBox="0 0 24 24" className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" fill="none" stroke="currentColor" strokeWidth="1.8">
        <circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" strokeLinecap="round" />
      </svg>
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        className="input h-8 pl-8 pr-3"
        placeholder="Search messages, UPI, phone, @channel…"
        aria-label="Search"
      />
    </form>
  );
}

function SystemStatus() {
  const [health, setHealth] = useState(null);
  useEffect(() => {
    api.get('/health').then(setHealth).catch(() => setHealth(null));
  }, []);
  if (!health) return <span className="chip text-[10px] text-danger">API offline</span>;
  const dots = [
    { label: 'Detection model', ok: health.model && health.model !== 'not-trained' },
    { label: `LLM · ${health.llm?.model || 'off'}`, ok: health.llm?.configured },
    { label: 'Telegram session', ok: health.telegram?.session },
    { label: 'Analyst bot', ok: health.telegram?.bot?.running || health.telegram?.bot?.enabled },
  ];
  return (
    <div className="hidden items-center gap-2.5 lg:flex" title={dots.map((d) => `${d.label}: ${d.ok ? 'on' : 'off'}`).join('\n')}>
      {dots.map((d) => (
        <span key={d.label} className="flex items-center gap-1.5 text-[10px] text-muted">
          <span className={cls('h-1.5 w-1.5 rounded-full', d.ok ? 'bg-accent live-dot' : 'bg-line')} />
          {d.label}
        </span>
      ))}
    </div>
  );
}

export default function Layout() {
  const { t } = useI18n();
  const loc = useLocation();
  const [theme, setTheme] = useTheme();
  const allItems = NAV.flatMap((g) => g.items);
  const current = allItems.find((n) => n.to === loc.pathname)?.label || t('nav.overview');

  return (
    <div className="flex min-h-full">
      <aside className="hidden w-[232px] shrink-0 flex-col border-r border-line bg-panel lg:flex">
        <div className="flex items-center gap-2.5 px-4 py-4">
          <Mark className="h-7 w-7 text-accent" />
          <div className="min-w-0">
            <div className="text-[15px] font-semibold leading-tight tracking-tight text-ink">SPECTRA</div>
            <div className="truncate text-[10px] text-muted">Rajasthan Police · OSINT</div>
          </div>
        </div>

        <nav className="mt-1 flex-1 overflow-y-auto px-2 pb-4">
          {NAV.map((group) => (
            <div key={group.label} className="mb-4">
              <div className="px-2 pb-1 text-[10px] font-medium text-muted">{group.label}</div>
              {group.items.map((n) => (
                <NavLink
                  key={n.to}
                  to={n.to}
                  end={n.to === '/'}
                  className={({ isActive }) => cls(
                    'mb-0.5 flex items-center gap-2.5 rounded border-l-2 px-2 py-1.5 text-[12.5px] transition-colors',
                    isActive
                      ? 'border-accent bg-accent/[0.07] font-medium text-ink'
                      : 'border-transparent text-muted hover:bg-raised/60 hover:text-ink',
                  )}
                >
                  <svg viewBox="0 0 24 24" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                    <path d={n.glyph} />
                  </svg>
                  {t(n.key) === n.key ? (n.label || n.key) : t(n.key)}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="border-t border-line px-4 py-3">
          <div className="flex items-center gap-1.5 text-[10px] text-muted">
            <span className="h-1.5 w-1.5 rounded-full bg-accent live-dot" />
            Public channels only
          </div>
          <div className="mt-1 text-[10px] text-muted">Read-only collection · evidence hashed</div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-12 items-center gap-3 border-b border-line bg-base/90 px-3 backdrop-blur lg:px-5">
          <div className="flex items-center gap-2 lg:hidden">
            <Mark className="h-5 w-5 text-accent" />
            <span className="text-[13px] font-semibold tracking-tight">SPECTRA</span>
          </div>
          <span className="hidden text-[12px] font-medium text-ink lg:block">{current}</span>
          <GlobalSearch />
          <div className="ml-auto flex items-center gap-1.5">
            <SystemStatus />
            <button
              className="btn btn-quiet px-2 py-1.5"
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
              title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
            >
              {theme === 'dark' ? (
                <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
                  <circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19" strokeLinecap="round" />
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
                  <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5Z" strokeLinejoin="round" />
                </svg>
              )}
            </button>
            <NotificationBell />
            <LangToggle />
          </div>
        </header>

        <div className="border-b border-line bg-panel/60 px-3 py-1.5 lg:hidden">
          <div className="flex gap-1 overflow-x-auto">
            {allItems.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === '/'}
                className={({ isActive }) => cls(
                  'whitespace-nowrap rounded px-2 py-1 text-[11px]',
                  isActive ? 'bg-accent/10 font-medium text-accent' : 'text-muted',
                )}
              >
                {t(n.key) === n.key ? (n.label || n.key) : t(n.key)}
              </NavLink>
            ))}
          </div>
        </div>

        <main key={loc.pathname} className="min-w-0 flex-1 px-3 py-4 lg:px-6 lg:py-5">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
