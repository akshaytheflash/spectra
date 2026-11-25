/**
 * Offline mock of the SPECTRA API for the statically hosted build.
 *
 * The Vercel deployment serves only the Vite bundle, so every /api-less
 * same-origin request would otherwise come back as index.html and render as
 * "API error". This module answers those requests from fixtures captured from
 * a real backend (see scripts/capture_mock.py) and keeps a small in-memory
 * store so status changes, joins and lexicon edits actually stick while the
 * page is open.
 *
 * Enabled by VITE_MOCK=1 (set in .env.production); the dev server keeps
 * talking to the real FastAPI backend.
 */

import { classifyPayload } from './mockClassifier.js';

export const MOCK_ENABLED = import.meta.env.VITE_MOCK === '1';

const FILES = import.meta.glob('./mockdata/*.json', { eager: true });
const fixture = (name) => {
  const mod = FILES[`./mockdata/${name}.json`];
  return mod ? (mod.default ?? mod) : undefined;
};

const clone = (v) => (v === undefined ? v : JSON.parse(JSON.stringify(v)));
const iso = () => new Date().toISOString();

const RISK_ORDER = { HIGH: 3, MEDIUM: 2, LOW: 1 };

// ---------------------------------------------------------------- fixtures

const detailFixtures = new Map();
const verifyFixtures = new Map();
for (let i = 0; i < 20; i += 1) {
  const d = fixture(`alert_${i}`);
  if (!d) continue;
  detailFixtures.set(d.id, d);
  const v = fixture(`alert_${i}_verify`);
  if (v) verifyFixtures.set(d.id, v);
}
const DETAIL_TEMPLATE = detailFixtures.values().next().value;

const RECENT = fixture('alerts_recent') || { items: [] };
const IDENTIFIERS = fixture('identifiers') || { items: [] };
const CAMPAIGNS = fixture('campaigns') || { items: [] };
const GRAPH = fixture('graph') || { nodes: [], edges: [], meta: {} };
const CHANNELS = fixture('channels') || { items: [] };
const LEXICON = fixture('lexicon') || { categories: {}, overlay: [] };

// ------------------------------------------------------------------ state

const recentAsAlerts = RECENT.items.map((r) => ({
  id: r.id,
  post_id: r.id,
  category: r.category,
  risk: r.risk,
  confidence: 0.9,
  status: r.status,
  channel: r.channel,
  timestamp: r.created_at,
  text: r.text,
  source_types: ['text'],
  identifier_types: [],
  cluster_id: null,
  media_type: null,
  has_evidence: true,
  flag_source: 'engine',
  llm_flag: false,
  emoji: [],
}));

/**
 * The graph and identity fixtures link to alerts that are not in the paged
 * /alerts response. Harvest them so those links open a real record instead of
 * a placeholder.
 */
function harvestAlerts(seed) {
  const seen = new Set(seed.map((a) => a.id));
  const out = [];
  const add = (row) => {
    if (!row.id || seen.has(row.id)) return;
    seen.add(row.id);
    out.push(row);
  };
  const riskFor = (cat) => (cat === 'benign' ? 'LOW' : 'HIGH');
  for (const n of GRAPH.nodes || []) {
    if (n.kind !== 'Post' || !n.alert_id) continue;
    add({
      id: n.alert_id,
      post_id: n.id,
      category: n.category || 'other_illegal',
      risk: riskFor(n.category),
      confidence: 0.95,
      status: 'new',
      channel: n.channel || 'unknown',
      timestamp: n.timestamp || null,
      text: n.label || '',
      source_types: ['text'],
      identifier_types: [],
      cluster_id: n.cluster_id ?? null,
      media_type: null,
      has_evidence: true,
      flag_source: 'engine',
      llm_flag: false,
      emoji: [],
    });
  }
  const identity = fixture('identity');
  const meta = new Map((identity?.alerts || []).map((a) => [a.id, a]));
  for (const p of identity?.posts || []) {
    if (!p.alert_id) continue;
    const a = meta.get(p.alert_id) || {};
    add({
      id: p.alert_id,
      post_id: p.id,
      category: p.category || a.category || 'other_illegal',
      risk: a.risk || riskFor(p.category),
      confidence: a.confidence ?? 0.95,
      status: a.status || 'new',
      channel: p.channel || 'unknown',
      timestamp: p.timestamp || null,
      text: p.text || '',
      source_types: ['text'],
      identifier_types: [],
      cluster_id: null,
      media_type: p.media_type ?? null,
      has_evidence: true,
      flag_source: 'engine',
      llm_flag: false,
      emoji: [],
    });
  }
  return out;
}

const seededAlerts = [...(fixture('alerts')?.items || []), ...recentAsAlerts];

const state = {
  alerts: [...seededAlerts, ...harvestAlerts(seededAlerts)],
  channels: clone(CHANNELS.items),
  identifiers: clone(IDENTIFIERS.items),
  lexiconOverlay: [],
  jobs: new Map(),
  jobPolls: new Map(),
};

const findAlert = (id) => state.alerts.find((a) => a.id === id);

// -------------------------------------------------------------- utilities

class MockError extends Error {
  constructor(status, detail) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

/** Deterministic 64-hex-char stand-in for SHA-256 (mock evidence hashes). */
function pseudoSha(seed) {
  const s = String(seed);
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;
  for (let i = 0; i < s.length; i += 1) {
    h1 = Math.imul(h1 ^ s.charCodeAt(i), 16777619) >>> 0;
    h2 = (Math.imul(h2 + s.charCodeAt(i) * (i + 7), 2654435761) >>> 0);
  }
  let out = '';
  let x = h1;
  let y = h2;
  for (let i = 0; i < 8; i += 1) {
    x = Math.imul(x ^ (x >>> 13), 2246822519) >>> 0;
    y = (y + 0x9e3779b9) >>> 0;
    out += ((x ^ y) >>> 0).toString(16).padStart(8, '0');
  }
  return out.slice(0, 64);
}

const fakeUuid = (seed) => {
  const h = pseudoSha(seed);
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20, 32)}`;
};

const sortRows = (rows, key, order) => {
  const dir = order === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    let x = a[key];
    let y = b[key];
    if (key === 'risk') {
      x = RISK_ORDER[a.risk] || 0;
      y = RISK_ORDER[b.risk] || 0;
    }
    if (typeof x === 'string') {
      x = x.toLowerCase();
      y = String(y ?? '').toLowerCase();
    }
    if (x === y) return 0;
    return (x > y ? 1 : -1) * dir;
  });
};

// ------------------------------------------------------------- alert views

function placeholderAlert(id) {
  return {
    id,
    post_id: id,
    category: 'other_illegal',
    risk: 'LOW',
    confidence: 0.5,
    status: 'new',
    channel: 'unknown',
    timestamp: null,
    text: 'This record sits outside the offline demo snapshot — the dashboard ships with a captured subset of the corpus.',
    source_types: [],
    identifier_types: [],
    cluster_id: null,
    media_type: null,
    has_evidence: false,
    flag_source: 'engine',
    llm_flag: false,
    emoji: [],
    placeholder: true,
  };
}

function detailFor(id) {
  const known = detailFixtures.get(id);
  if (known) {
    const d = clone(known);
    const live = findAlert(id);
    if (live) d.status = live.status;
    return d;
  }
  // Alert links also come from graph nodes, shared URLs and notifications, so
  // an id outside the snapshot must still render rather than blow up React.
  const item = findAlert(id) || placeholderAlert(id);
  // No captured detail for this alert: reuse the captured *structure* but
  // swap in the real text, and drop spans whose offsets belong to another
  // post so <HighlightedText/> cannot render garbage.
  const d = clone(DETAIL_TEMPLATE);
  d.id = item.id;
  d.category = item.category;
  d.risk = item.risk;
  d.confidence = item.confidence;
  d.status = item.status;
  d.flag_source = item.flag_source || 'engine';
  d.cluster_id = item.cluster_id ?? null;
  d.post = {
    ...d.post,
    id: item.post_id || item.id,
    channel: item.channel,
    timestamp: item.timestamp,
    text: item.text,
    permalink: `https://t.me/${item.channel}/`,
    media_path: null,
    media_type: item.media_type ?? null,
  };
  d.explanation = { model_used: false, model_version: 'spectra-hybrid-v1' };
  d.subcategories = {};
  d.identifiers = [];
  d.audit = [];
  d.analyst_notes = [];
  d.related = {};
  d.emoji = [];
  d.llm = { available: false, error: 'not-run', model: '' };
  const capturedAt = item.timestamp || iso();
  const sha256 = pseudoSha(`spectra-evidence-${item.id}`);
  d.evidence = {
    id: fakeUuid(`ev-${item.id}`),
    payload: {
      post_id: item.post_id || item.id,
      channel: item.channel,
      post_text: item.text,
      timestamp: capturedAt,
      permalink: d.post.permalink,
      media_path: null,
      media_sha256: null,
      identifiers: [],
      classification: { category: item.category, risk: item.risk, confidence: item.confidence },
      explanation: {},
      captured_at: capturedAt,
      extra: {},
    },
    sha256,
    captured_at: capturedAt,
  };
  return d;
}

function verifyFor(id) {
  const known = verifyFixtures.get(id);
  if (known) return clone(known);
  const detail = detailFor(id);
  return {
    ok: true,
    stored_sha256: detail.evidence?.sha256 || '',
    recomputed_sha256: detail.evidence?.sha256 || '',
    algorithm: 'SHA-256 (canonical JSON: sorted keys, no whitespace, utf-8)',
    fields: ['captured_at', 'channel', 'classification', 'post_id', 'post_text', 'timestamp'],
    verified_at: iso(),
  };
}

function listAlerts(q) {
  const category = q.get('category') || '';
  const risk = (q.get('risk') || '').toUpperCase();
  const status = q.get('status') || '';
  const channel = q.get('channel') || '';
  const identifierType = q.get('identifier_type') || '';
  const source = q.get('source') || '';
  const needle = (q.get('q') || '').toLowerCase();
  const dateFrom = q.get('date_from') || '';
  const dateTo = q.get('date_to') || '';
  const sort = q.get('sort') || 'timestamp';
  const order = q.get('order') || 'desc';
  const page = Math.max(1, parseInt(q.get('page') || '1', 10) || 1);
  const pageSize = Math.min(500, Math.max(1, parseInt(q.get('page_size') || '50', 10) || 50));

  const rows = state.alerts.filter((a) => {
    if (category && a.category !== category) return false;
    if (risk && a.risk !== risk) return false;
    if (status && a.status !== status) return false;
    if (channel && a.channel !== channel) return false;
    if (identifierType && !(a.identifier_types || []).includes(identifierType)) return false;
    if (source && !(a.source_types || []).includes(source)) return false;
    if (needle && !`${a.text} ${a.channel} ${a.category}`.toLowerCase().includes(needle)) return false;
    if (dateFrom && String(a.timestamp) < dateFrom) return false;
    if (dateTo && String(a.timestamp) > `${dateTo}T23:59:59`) return false;
    return true;
  });

  const sorted = sortRows(rows, sort, order);
  return {
    total: sorted.length,
    page,
    page_size: pageSize,
    items: sorted.slice((page - 1) * pageSize, page * pageSize),
  };
}

function recentView() {
  return {
    ...RECENT,
    items: RECENT.items.map((r) => {
      const live = findAlert(r.id);
      return live ? { ...r, status: live.status } : r;
    }),
    count: RECENT.items.length,
  };
}

// ------------------------------------------------------------ other views

function filterChannels(q) {
  const monitored = q.get('monitored');
  const needle = (q.get('q') || '').toLowerCase();
  const limit = Math.min(500, parseInt(q.get('limit') || '200', 10) || 200);
  let rows = state.channels;
  if (monitored === 'true') rows = rows.filter((c) => c.is_monitored);
  if (monitored === 'false') rows = rows.filter((c) => !c.is_monitored);
  if (needle) rows = rows.filter((c) => `${c.username} ${c.title || ''}`.toLowerCase().includes(needle));
  return { items: rows.slice(0, limit) };
}

function filterIdentifiers(q) {
  const needle = (q.get('q') || '').toLowerCase();
  const type = q.get('type') || '';
  const channel = q.get('channel') || '';
  const limit = parseInt(q.get('limit') || '500', 10) || 500;
  const rows = state.identifiers.filter((r) => {
    if (type && r.type !== type) return false;
    if (channel && !(r.channels || []).includes(channel)) return false;
    if (needle && !`${r.value} ${(r.channels || []).join(' ')}`.toLowerCase().includes(needle)) return false;
    return true;
  });
  return { total: rows.length, items: rows.slice(0, limit) };
}

function graphView(q) {
  const ident = q.get('identifier');
  if (!ident || !GRAPH.edges.length) return GRAPH;
  const hops = Math.min(4, Math.max(1, parseInt(q.get('hops') || '2', 10) || 2));
  const kept = new Set([ident]);
  let frontier = new Set([ident]);
  for (let h = 0; h < hops; h += 1) {
    const next = new Set();
    for (const e of GRAPH.edges) {
      if (frontier.has(e.source) && !kept.has(e.target)) {
        kept.add(e.target);
        next.add(e.target);
      }
      if (frontier.has(e.target) && !kept.has(e.source)) {
        kept.add(e.source);
        next.add(e.source);
      }
    }
    if (!next.size) break;
    frontier = next;
  }
  const nodes = GRAPH.nodes.filter((n) => kept.has(n.id));
  if (!nodes.length) return GRAPH;
  return {
    nodes,
    edges: GRAPH.edges.filter((e) => kept.has(e.source) && kept.has(e.target)),
    meta: { ...GRAPH.meta, focused: ident, hops },
  };
}

function searchView(q) {
  const needle = (q.get('q') || '').trim().toLowerCase();
  const channel = (q.get('channel') || '').toLowerCase();
  const category = q.get('category') || '';
  const dateFrom = q.get('date_from') || '';
  const dateTo = q.get('date_to') || '';
  const rows = state.alerts.filter((a) => {
    if (channel && a.channel.toLowerCase() !== channel) return false;
    if (category && a.category !== category) return false;
    if (dateFrom && String(a.timestamp) < dateFrom) return false;
    if (dateTo && String(a.timestamp) > `${dateTo}T23:59:59`) return false;
    if (!needle) return true;
    return `${a.text} ${a.channel} ${a.category}`.toLowerCase().includes(needle);
  });
  const hits = rows.map((a) => ({
    post_id: a.post_id || a.id,
    alert_id: a.id,
    channel: a.channel,
    timestamp: a.timestamp,
    text: a.text,
    category: a.category,
    label: a.category,
    score: a.confidence ?? 0.9,
    matched: needle && a.text.toLowerCase().includes(needle) ? [q.get('q')] : [],
  }));
  return { query: q.get('q') || '', mode: q.get('mode') || 'auto', total: hits.length, hits };
}

function lexiconView() {
  return { version: LEXICON.version, categories: LEXICON.categories, overlay: clone(state.lexiconOverlay) };
}

function identityFor(value) {
  const row = state.identifiers.find((r) => r.value === value);
  const captured = fixture('identity');
  if (captured && captured.value === value) return clone(captured);
  const hits = state.alerts.filter((a) => a.text.includes(value) || a.text.includes(value.replace(/^\+91/, '')));
  const base = {
    value,
    type: row?.type || 'unknown',
    aliases: [],
    channels: row?.channels || [],
    upis: row?.type === 'upi' ? [value] : [],
    phones: row?.type === 'phone' ? [value] : [],
    wallets: row?.type === 'wallet' ? [value] : [],
    handles: row?.type === 'username' ? [value] : [],
    payee_names: [],
    first_seen: row?.first_seen || null,
    last_seen: row?.last_seen || null,
    risk_score: row?.risk_score ?? 0,
    categories: row?.categories || [],
    posts: hits.map((a) => ({
      id: a.post_id || a.id,
      channel: a.channel,
      timestamp: a.timestamp,
      text: a.text,
      permalink: `https://t.me/${a.channel}/`,
      media_type: null,
      category: a.category,
      risk: a.risk,
      alert_id: a.id,
    })),
    alerts: hits.map((a) => ({
      id: a.id,
      category: a.category,
      risk: a.risk,
      confidence: a.confidence,
      status: a.status,
      created_at: a.timestamp,
    })),
  };
  return base;
}

// ------------------------------------------------------------------ jobs

function startJob(kind, total, message, result) {
  const id = fakeUuid(`${kind}-${Date.now()}-${Math.random()}`);
  const job = {
    id,
    kind,
    status: 'running',
    total,
    done: 0,
    message: message || 'Starting…',
    result: result || {},
    started_at: iso(),
    finished_at: null,
  };
  state.jobs.set(id, job);
  state.jobPolls.set(id, 0);
  return job;
}

function jobView(id) {
  const job = state.jobs.get(id);
  if (!job) {
    return {
      id,
      kind: 'unknown',
      status: 'completed',
      total: 1,
      done: 1,
      message: 'Already finished.',
      result: {},
      started_at: null,
      finished_at: iso(),
    };
  }
  const polls = (state.jobPolls.get(id) || 0) + 1;
  state.jobPolls.set(id, polls);
  if (job.status === 'running') {
    if (polls === 1 && job.total > 1) {
      job.done = Math.max(1, Math.ceil(job.total / 2));
      job.message = 'Analysing collected messages…';
    } else {
      job.status = 'completed';
      job.done = job.total;
      job.message = 'Completed (offline demo).';
      job.finished_at = iso();
    }
  }
  return clone(job);
}

const ingestResult = (channels) => ({
  posts: 24,
  alerts: 3,
  mode: 'demo',
  channels: (channels || ['demo_channel']).map((username) => ({ username, title: username })),
});

// ---------------------------------------------------------- channel ops

function newChannel(username, monitored) {
  const seed = pseudoSha(username);
  const subs = 5000 + (parseInt(seed.slice(0, 6), 16) % 400000);
  return {
    id: fakeUuid(`channel-${username}`),
    username,
    title: username,
    description: 'Public channel monitored for narcotics, arms and financial-crime listings.',
    is_public: true,
    is_monitored: !!monitored,
    join_status: 'pending',
    subscriber_count: subs,
    last_scraped: null,
    posts: 0,
    alerts: 0,
    added_by: 'manual',
  };
}

function joinChannel(body) {
  const target = String(body?.target || '')
    .trim()
    .replace(/^https?:\/\/(www\.)?t\.me\//i, '')
    .replace(/^@/, '')
    .split(/[/?#]/)[0];
  if (!target) throw new MockError(400, 'invalid public channel target');
  const existing = state.channels.find((c) => c.username.toLowerCase() === target.toLowerCase());
  if (existing) {
    return { ok: true, queued: false, reason: 'Already on the watchlist.', channel: clone(existing) };
  }
  const ch = newChannel(target, body?.monitor);
  state.channels.unshift(ch);
  return {
    ok: false,
    queued: true,
    reason: 'Telegram session not configured; channel saved and queued for joining.',
    channel: clone(ch),
  };
}

function discoverChannels(q) {
  const needle = (q.get('q') || '').trim().toLowerCase();
  const limit = parseInt(q.get('limit') || '12', 10) || 12;
  const pool = [
    { username: 'ganja_direct_pune', title: 'Ganja direct supply', about: 'Narcotics listings, Pune hub.', subscribers: 41230 },
    { username: 'katta_tamancha_bazar', title: 'Katta & tamancha bazaar', about: 'Country-made arms.', subscribers: 18740 },
    { username: 'mule_account_rent', title: 'Mule account rent', about: 'Bank account / ATM card rent ads.', subscribers: 62110 },
    { username: 'fake_aadhaar_docs', title: 'Fake Aadhaar & PAN', about: 'Ready-made identity documents.', subscribers: 27455 },
    { username: 'netbanking_kit_store', title: 'Net-banking kits', about: 'Token + credentials kits.', subscribers: 9980 },
    { username: 'sim_otp_supplier', title: 'SIM & OTP supply', about: 'Bulk SIMs for OTP relay.', subscribers: 33890 },
    { username: 'mdma_lsd_premium', title: 'MDMA / LSD premium', about: 'Party drugs, pan-India delivery.', subscribers: 51200 },
    { username: 'delhi_night_supply', title: 'Delhi night supply', about: 'Night delivery narcotics.', subscribers: 12040 },
  ];
  const known = new Set(state.channels.map((c) => c.username.toLowerCase()));
  const items = pool
    .filter((c) => !needle || `${c.username} ${c.title} ${c.about}`.toLowerCase().includes(needle))
    .map((c) => ({ ...c, known: known.has(c.username), joined: false }))
    .slice(0, limit);
  return { ok: true, reason: null, items };
}

function discoverAndJoin(body) {
  const keywords = (body?.keywords || ['ganja', 'katta', 'mule account']).slice(0, 8);
  const joined = [];
  for (const kw of keywords) {
    const username = `${kw.replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '')}_hub`;
    if (state.channels.some((c) => c.username.toLowerCase() === username)) continue;
    const ch = newChannel(username, !!body?.join);
    state.channels.push(ch);
    joined.push({ username: ch.username, title: ch.title });
  }
  return { ok: true, joined, results: [], keywords };
}

// ---------------------------------------------------------- text exports

const PLACEHOLDER_IMAGE =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#141a19"/><rect x="8" y="8" width="624" height="344" fill="none" stroke="#2f8c77" stroke-width="2" stroke-dasharray="8 6"/><text x="320" y="188" fill="#7d8a83" font-family="monospace" font-size="18" text-anchor="middle">media evidence</text></svg>`,
  );

function identifiersCsv() {
  const head = [
    'type', 'value', 'post_count', 'channel_count', 'channels', 'categories',
    'first_seen', 'last_seen', 'risk_score', 'alert_count', 'campaign_count',
  ];
  const esc = (v) => {
    const s = Array.isArray(v) ? v.join('|') : String(v ?? '');
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [head.join(','), ...state.identifiers.map((r) => head.map((h) => esc(r[h])).join(','))].join('\r\n');
}

/** Minimal single-page PDF so the "PDF report" links really download something. */
function buildPdf(title, lines) {
  const esc = (s) =>
    String(s)
      .replace(/[^\x20-\x7E]/g, '?')
      .replace(/\\/g, '\\\\')
      .replace(/\(/g, '\\(')
      .replace(/\)/g, '\\)');
  let stream = `BT /F1 17 Tf 48 790 Td (${esc(title)}) Tj ET\n`;
  let y = 764;
  stream += `BT /F1 9 Tf 48 ${y} Td\n`;
  for (const line of lines.slice(0, 44)) {
    stream += `0 -14 Td (${esc(line)}) Tj\n`;
    y -= 14;
    if (y < 56) break;
  }
  stream += 'ET\n';
  const objs = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
    `<< /Length ${stream.length} >>\nstream\n${stream}endstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ];
  let pdf = '%PDF-1.4\n';
  const offsets = [];
  objs.forEach((o, i) => {
    offsets.push(pdf.length);
    pdf += `${i + 1} 0 obj\n${o}\nendobj\n`;
  });
  const xref = pdf.length;
  pdf += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n`;
  for (const off of offsets) pdf += `${String(off).padStart(10, '0')} 00000 n \n`;
  pdf += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return pdf;
}

/**
 * Build the downloadable report for either an alert id or a campaign id.
 * Called from JSX during render, so it must never throw.
 */
function reportFor(id) {
  const campaign = CAMPAIGNS.items.find((c) => c.id === id);
  if (campaign) {
    return buildPdf('SPECTRA - campaign report', [
      `Campaign    ${campaign.title}`,
      `Category    ${campaign.category}`,
      `Channels    ${(campaign.channels || []).join(', ')}`,
      `Posts       ${campaign.post_count}   Alerts ${campaign.alert_count}`,
      `Similarity  ${Math.round((campaign.similarity || 0) * 100)}%`,
      `Window      ${campaign.time_span || '-'}`,
      '',
      'Shared identifiers',
      '------------------',
      ...(campaign.shared_identifiers || []).slice(0, 20),
      '',
      'Generated by SPECTRA (offline demo build).',
    ]);
  }
  const item = findAlert(id);
  if (!item && !detailFixtures.has(id)) {
    return buildPdf('SPECTRA - report', [
      `Reference   ${id}`,
      '',
      'This record sits outside the offline demo snapshot.',
      'Generated by SPECTRA (offline demo build).',
    ]);
  }
  const detail = detailFor(id);
  const lines = [
    `Alert        ${id}`,
    `Channel      @${detail.post?.channel || '-'}`,
    `Category     ${detail.category}   Risk ${detail.risk}   Confidence ${Math.round((detail.confidence || 0) * 100)}%`,
    `Status       ${detail.status}   Source ${detail.flag_source}`,
    `Captured     ${detail.evidence?.captured_at || '-'}`,
    `Evidence SHA ${detail.evidence?.sha256 || '-'}`,
    '',
    'Message',
    '-------',
    String(detail.post?.text || '').slice(0, 300),
    '',
    'Why flagged',
    '-----------',
    ...((detail.explanation?.reasons || []).slice(0, 12)),
    '',
    'Generated by SPECTRA (offline demo build).',
  ];
  return buildPdf('SPECTRA - alert evidence report', lines);
}

// ---------------------------------------------------------------- routing

function route(method, rawPath, q, body) {
  const path = rawPath.split('?')[0];
  let m;

  if (method === 'GET') {
    if (path === '/health') return fixture('health');
    if (path === '/stats') return fixture('stats');
    if (path === '/metrics') return fixture('metrics');
    if (path === '/ops/status') return fixture('ops_status');
    if (path === '/realtime/config') return fixture('realtime_config');
    if (path === '/map') return fixture('map');
    if (path === '/alerts/recent') return recentView();
    if (path === '/alerts') return listAlerts(q);
    if (path === '/campaigns') return { total: CAMPAIGNS.items.length, items: CAMPAIGNS.items };
    if (path === '/identifiers') return filterIdentifiers(q);
    if (path === '/channels') return filterChannels(q);
    if (path === '/channels/status') return fixture('channels_status');
    if (path === '/channels/names') return fixture('channels_names');
    if (path === '/channels/discover') return discoverChannels(q);
    if (path === '/search') return searchView(q);
    if (path === '/graph') return graphView(q);
    if (path === '/lexicon') return lexiconView();
    if (path === '/ingest/jobs') return { items: [...state.jobs.values()].map((j) => clone(j)) };

    if ((m = /^\/alerts\/([^/]+)\/verify$/.exec(path))) return verifyFor(decodeURIComponent(m[1]));
    if ((m = /^\/alerts\/([^/]+)$/.exec(path))) return detailFor(decodeURIComponent(m[1]));
    if ((m = /^\/campaigns\/([^/]+)$/.exec(path))) {
      const found = CAMPAIGNS.items.find((c) => c.id === m[1]);
      if (!found) {
        return {
          id: m[1],
          category: 'other_illegal',
          post_count: 0,
          channels: [],
          shared_identifiers: [],
          time_span: '-',
          posting_frequency: 0,
          similarity: 0,
          category_mix: {},
          title: 'Not in the offline demo snapshot',
          snippet: 'This campaign sits outside the captured corpus.',
          alert_count: 0,
          members: [],
        };
      }
      return found;
    }
    if ((m = /^\/identity\/(.+)$/.exec(path))) return identityFor(decodeURIComponent(m[1]));
    if ((m = /^\/ingest\/jobs\/([^/]+)$/.exec(path))) return jobView(m[1]);

    throw new MockError(404, `mock has no route for GET ${path}`);
  }

  if (method === 'POST') {
    if (path === '/classify') return classifyPayload(body?.text ?? '', state.lexiconOverlay);
    if (path === '/feedback') return { ok: true, saved: 'data/generated/analyst_feedback.jsonl' };
    if (path === '/ops/sweep') {
      return { ok: true, messages: 37, alerts: 4, channels: state.channels.filter((c) => c.is_monitored).length };
    }
    if (path === '/ops/discover-join') return discoverAndJoin({});
    if (path === '/channels/join') return joinChannel(body);
    if (path === '/channels/discover') return discoverAndJoin(body);
    if (path === '/lexicon/reload') return { ok: true };

    if (path === '/lexicon/terms') {
      const term = {
        id: fakeUuid(`term-${Date.now()}-${Math.random()}`),
        category: body?.category || 'drugs',
        term: String(body?.term || '').trim(),
        kind: body?.kind || 'slang',
        weight: Number(body?.weight) || 1,
        language: 'en',
        created_by: 'analyst',
        created_at: iso(),
      };
      if (!term.term) throw new MockError(422, 'term is required');
      state.lexiconOverlay.push(term);
      return { ok: true, id: term.id, reloaded: true };
    }

    if (path === '/ingest/telegram') {
      const targets = body?.targets || [];
      const usernames = targets.map((t) => String(t).replace(/^https?:\/\/t\.me\//, '').replace(/^@/, '')).filter(Boolean);
      return startJob('telegram_ingest', Math.max(1, Number(body?.limit) || 50), 'Collecting from public channels…', ingestResult(usernames));
    }
    if (path === '/ingest/file') {
      return { job: startJob('file_ingest', 40, 'Uploading and parsing…', ingestResult(['uploaded_batch'])), files: ['upload.csv'] };
    }
    if (path === '/ingest/media') return startJob('media_ingest', 10, 'Downloading media…', ingestResult());
    if (path === '/pipeline/run') return startJob('pipeline', 40, 'Running pipeline…', ingestResult());

    if (path === '/analyze/image') {
      const classification = classifyPayload(
        'Ready made Aadhaar and PAN card available. same day delivery. telegram @docs_quick_ncr 9000637940',
        state.lexiconOverlay,
      );
      return {
        ocr: {
          text: 'AADHAAR 5842 7719 0031\nName: SAMPLE VERIFIED\nReady made documents, same day delivery\n+91 90006 37940',
          language: 'en',
          confidence: 0.93,
          boxes: [],
          preprocessed_paths: [],
        },
        classification,
        identifiers: classification.identifiers,
        text: classification.text,
      };
    }
    if (path === '/analyze/audio') {
      const classification = classifyPayload(
        'bhai maal ready hai, ganja aur charas dono, delivery Pune tak. telegram @ganja_direct_pune 9876543210',
        state.lexiconOverlay,
      );
      return {
        transcript: {
          text: classification.text,
          language: 'hi',
          language_probability: 0.91,
          model: 'faster-whisper small',
          duration: 6.4,
          segments: [{ start: 0, end: 6.4, text: classification.text }],
        },
        classification,
        identifiers: classification.identifiers,
      };
    }

    if ((m = /^\/channels\/([^/]+)\/monitor$/.exec(path))) {
      const ch = state.channels.find((c) => c.id === m[1]);
      if (!ch) throw new MockError(404, 'channel not found');
      ch.is_monitored = !!body?.enabled;
      return { ok: true, channel: clone(ch) };
    }
    if ((m = /^\/channels\/([^/]+)\/scan$/.exec(path))) {
      const ch = state.channels.find((c) => c.id === m[1]);
      if (!ch) throw new MockError(404, 'channel not found');
      return startJob('channel_scan', Math.max(1, parseInt(q.get('limit') || '50', 10) || 50), `Scanning @${ch.username}…`, ingestResult([ch.username]));
    }
    if ((m = /^\/alerts\/([^/]+)\/rescan$/.exec(path))) {
      const id = decodeURIComponent(m[1]);
      const detail = detailFor(id);
      const verdict = classifyPayload(detail.post?.text || '', state.lexiconOverlay);
      const item = findAlert(id);
      if (item) {
        item.category = verdict.category;
        item.risk = verdict.risk;
        item.confidence = verdict.confidence;
      }
      return {
        ok: true,
        flag_source: 'engine',
        category: verdict.category,
        risk: verdict.risk,
        confidence: verdict.confidence,
        flagged: verdict.is_illicit,
        llm: verdict.explanation.llm,
        emoji: verdict.explanation.emoji_signals,
      };
    }

    throw new MockError(404, `mock has no route for POST ${path}`);
  }

  if (method === 'PATCH') {
    if ((m = /^\/alerts\/([^/]+)\/status$/.exec(path))) {
      const id = decodeURIComponent(m[1]);
      const item = findAlert(id);
      const next = body?.status;
      if (!['new', 'under_review', 'confirmed', 'dismissed'].includes(next)) {
        throw new MockError(422, 'invalid status');
      }
      if (!item) return { ok: true, status: next, audit_logged: true };
      const same = item.status === next;
      item.status = next;
      if (body?.notes && !same) {
        item.analyst_notes = [
          ...(item.analyst_notes || []),
          { actor: body.actor || 'analyst', notes: body.notes, at: iso(), status: next },
        ];
      }
      return same ? { ok: true, status: next } : { ok: true, status: next, audit_logged: true };
    }
    throw new MockError(404, `mock has no route for PATCH ${path}`);
  }

  if (method === 'DELETE') {
    if ((m = /^\/lexicon\/terms\/([^/]+)$/.exec(path))) {
      const before = state.lexiconOverlay.length;
      state.lexiconOverlay = state.lexiconOverlay.filter((t) => t.id !== m[1]);
      if (state.lexiconOverlay.length === before) throw new MockError(404, 'term not found');
      return { ok: true };
    }
    if ((m = /^\/channels\/([^/]+)\/join$/.exec(path))) {
      const ch = state.channels.find((c) => c.id === m[1]);
      if (!ch) throw new MockError(404, 'channel not found');
      ch.join_status = 'left';
      ch.is_monitored = false;
      return { ok: true, channel: clone(ch) };
    }
    throw new MockError(404, `mock has no route for DELETE ${path}`);
  }

  throw new MockError(405, `mock has no route for ${method} ${path}`);
}

// ------------------------------------------------------------- entrypoints

export async function mockRequest(method, url, opts = {}) {
  const path = String(url || '/');
  const queryStart = path.indexOf('?');
  const q = new URLSearchParams(queryStart >= 0 ? path.slice(queryStart + 1) : '');
  let body = null;
  if (opts.body && typeof opts.body === 'string') {
    try {
      body = JSON.parse(opts.body);
    } catch {
      body = null;
    }
  }
  try {
    return clone(route(method.toUpperCase(), path, q, body));
  } catch (e) {
    if (e instanceof MockError) throw new Error(e.message);
    throw e;
  }
}

export async function mockUpload(path) {
  const p = String(path || '');
  try {
    if (p === '/analyze/image' || p === '/analyze/audio') {
      return clone(route('POST', p, new URLSearchParams(), null));
    }
    if (p === '/ingest/file') return clone(route('POST', p, new URLSearchParams(), null));
    return clone(route('POST', p, new URLSearchParams(), null));
  } catch (e) {
    if (e instanceof MockError) throw new Error(e.message);
    throw e;
  }
}

export function mockMediaUrl(p) {
  if (!p) return null;
  if (String(p).startsWith('http')) return p;
  return PLACEHOLDER_IMAGE;
}

export function mockRawUrl(p) {
  const path = String(p || '');
  if (path === '/identifiers/export.csv') {
    const csv = identifiersCsv();
    return `data:text/csv;charset=utf-8,${encodeURIComponent(csv)}`;
  }
  const report = /^\/report\/(.+)\.pdf$/.exec(path);
  if (report) {
    const pdf = buildPdfB64(reportFor(report[1]));
    return `data:application/pdf;base64,${pdf}`;
  }
  if (path.startsWith('/media/')) return PLACEHOLDER_IMAGE;
  return path;
}

function buildPdfB64(pdf) {
  // btoa only accepts Latin-1; buildPdf already strips everything else.
  if (typeof btoa === 'function') return btoa(pdf);
  const bytes = new Uint8Array(pdf.length);
  for (let i = 0; i < pdf.length; i += 1) bytes[i] = pdf.charCodeAt(i) & 0xff;
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}
