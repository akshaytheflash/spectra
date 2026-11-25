// Thin API client + shared helpers.
//
// The backend may be (a) the same origin as the API, (b) the Vite dev server
// proxying /api -> :8000, or (c) a separately hosted static build pointed at
// the API via VITE_API_URL.
import { MOCK_ENABLED, mockMediaUrl, mockRawUrl, mockRequest, mockUpload } from './mock.js';

const EXPLICIT = import.meta.env.VITE_API_URL;
const DEV = import.meta.env.DEV;
const BASE = EXPLICIT !== undefined && EXPLICIT !== '' ? EXPLICIT : (DEV ? '/api' : '');

function withTimeout(ms) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  return { signal: ctrl.signal, done: () => clearTimeout(timer) };
}

async function req(path, opts = {}) {
  if (MOCK_ENABLED) return mockRequest(opts.method || 'GET', path, opts);
  const t = withTimeout(opts.timeout || 60000);
  try {
    const res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
      cache: 'no-store',
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
    if (MOCK_ENABLED) return mockUpload(path, form);
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
  mediaUrl: (p) => {
    if (MOCK_ENABLED) return mockMediaUrl(p);
    if (!p) return null;
    if (p.startsWith('http')) return p;
    const name = p.split('/').pop();
    return `${BASE}/media/posts/${name}`;
  },
  rawUrl: (p) => (MOCK_ENABLED ? mockRawUrl(p) : `${BASE}${p}`),
};

// Mid-tone chart palette: legible on both the paper and the night theme.
export const CATEGORY_COLORS = {
  drugs: '#b45c86',
  arms: '#c25448',
  fake_docs: '#b08a35',
  mule_account: '#3f8c77',
  atm_card: '#5a87ac',
  sim_card: '#8272ba',
  net_banking_kit: '#4e979c',
  mule_fin: '#3f8c77',
  other_illegal: '#8a6b2f',
  benign: '#7d8a83',
};

export const RISK_COLORS = {
  HIGH: 'rgb(var(--c-danger))',
  MEDIUM: 'rgb(var(--c-warn))',
  LOW: 'rgb(var(--c-muted))',
};

export const RISK_ORDER = { HIGH: 3, MEDIUM: 2, LOW: 1 };

export const CATEGORY_LABELS = {
  drugs: 'Narcotics',
  arms: 'Arms',
  fake_docs: 'Fake documents',
  mule_account: 'Mule account',
  atm_card: 'ATM / debit card',
  sim_card: 'SIM card',
  net_banking_kit: 'Net-banking kit',
  mule_fin: 'Mule / financial',
  other_illegal: 'Other illegal',
  benign: 'Benign',
};

export const FLAG_SOURCE_LABELS = {
  engine: 'Engine',
  llm: 'GLM',
  'engine+llm': 'Engine + GLM',
  emoji: 'Emoji signal',
  'llm-veto': 'GLM cleared',
};

export const NODE_COLORS = {
  Post: '#6f7d76',
  Channel: '#2f8c77',
  Phone: '#b45c86',
  UPI: '#71923a',
  Wallet: '#b08a35',
  Username: '#8272ba',
  Location: '#5a87ac',
  Bank: '#c25448',
  Link: '#8a948d',
  ImageHash: '#7d8a83',
  AudioHash: '#7d8a83',
};

export const fmtDate = (d) => {
  if (!d) return '—';
  const dt = new Date(d);
  if (Number.isNaN(dt.getTime())) return String(d);
  return dt.toLocaleString(undefined, { year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
};
export const fmtDay = (d) => (d ? new Date(d).toLocaleDateString(undefined, { month: 'short', day: '2-digit' }) : '—');
export const fmtTime = (d) => {
  if (!d) return '—';
  const dt = new Date(d);
  if (Number.isNaN(dt.getTime())) return String(d);
  return dt.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
};
export const timeAgo = (d) => {
  if (!d) return '—';
  const dt = new Date(d);
  if (Number.isNaN(dt.getTime())) return String(d);
  const s = Math.max(1, Math.round((Date.now() - dt.getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};
export const pct = (v) => `${Math.round((v || 0) * 100)}%`;
export const cls = (...xs) => xs.filter(Boolean).join(' ');
