// Thin API client + tiny helpers shared by all pages.
//
// The backend may be (a) the same origin as the API, (b) the Vite dev server
// proxying /api -> :8000, or (c) a separately hosted static build pointed at
// the API via VITE_API_URL. All three are supported below.
// Dev server proxies /api -> FastAPI (see vite.config.js); a static build is
// served by the API on the same origin; VITE_API_URL overrides both.
const EXPLICIT = import.meta.env.VITE_API_URL;
const DEV = import.meta.env.DEV;
const BASE = EXPLICIT !== undefined && EXPLICIT !== '' ? EXPLICIT : (DEV ? '/api' : '');

function withTimeout(ms) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  return { signal: ctrl.signal, done: () => clearTimeout(timer) };
}

async function req(path, opts = {}) {
  const t = withTimeout(opts.timeout || 45000);
  try {
    const res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
      signal: t.signal,
      ...opts,
    });
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try { detail = (await res.json()).detail || detail; } catch { /* not json */ }
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    if (opts.raw) return res;
    return res.json();
  } catch (e) {
    if (e.name === 'AbortError') throw new Error(`request timed out: ${path}`);
    throw e;
  } finally {
    t.done();
  }
}

export const api = {
  get: (p, opts = {}) => req(p, opts),
  post: (p, body, opts = {}) => req(p, { ...opts, method: 'POST', body: JSON.stringify(body) }),
  patch: (p, body) => req(p, { method: 'PATCH', body: JSON.stringify(body) }),
  del: (p) => req(p, { method: 'DELETE' }),
  upload: async (path, form, opts = {}) => {
    const t = withTimeout(opts.timeout || 300000);
    const { timeout, ...rest } = opts;
    try {
      const res = await fetch(`${BASE}${path}`, { method: 'POST', body: form, signal: t.signal, ...rest });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
      return res.json();
    } finally {
      t.done();
    }
  },
  // Evidence media: /media/posts/<name> on the API origin.
  mediaUrl: (p) => {
    if (!p) return null;
    if (p.startsWith('http')) return p;
    const name = p.split('/').pop();
    return `${BASE}/media/posts/${name}`;
  },
  rawUrl: (p) => `${BASE}${p}`,
};

export const CATEGORY_COLORS = {
  drugs: '#f472b6',
  arms: '#fb7185',
  fake_docs: '#fbbf24',
  mule_account: '#34d399',
  atm_card: '#22d3ee',
  sim_card: '#a78bfa',
  net_banking_kit: '#38bdf8',
  mule_fin: '#34d399',
  benign: '#64748b',
};

export const RISK_COLORS = { HIGH: '#f43f5e', MEDIUM: '#f59e0b', LOW: '#64748b' };

export const CATEGORY_LABELS = {
  drugs: 'Narcotics',
  arms: 'Arms',
  fake_docs: 'Fake documents',
  mule_account: 'Mule account',
  atm_card: 'ATM / debit card',
  sim_card: 'SIM card',
  net_banking_kit: 'Net-banking kit',
  mule_fin: 'Mule / financial',
  benign: 'Benign',
};

export const NODE_COLORS = {
  Post: '#3b4a63',
  Channel: '#22d3ee',
  Phone: '#f472b6',
  UPI: '#a3e635',
  Wallet: '#fbbf24',
  Username: '#c084fc',
  Location: '#38bdf8',
  Bank: '#fda4af',
  Link: '#94a3b8',
  ImageHash: '#64748b',
  AudioHash: '#64748b',
};

export const fmtDate = (d) => {
  if (!d) return '—';
  const dt = new Date(d);
  if (Number.isNaN(dt.getTime())) return String(d);
  return dt.toLocaleString(undefined, { year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
};
export const fmtDay = (d) => (d ? new Date(d).toLocaleDateString(undefined, { month: 'short', day: '2-digit' }) : '—');
export const pct = (v) => `${Math.round((v || 0) * 100)}%`;
export const cls = (...xs) => xs.filter(Boolean).join(' ');
