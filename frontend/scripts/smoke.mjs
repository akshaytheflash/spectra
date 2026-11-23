// Render every page in jsdom with stubbed API responses; fail on render errors.
// Canvas-based pages (Entity Graph = Cytoscape, Map = Leaflet) cannot run their
// layout engines headless; they are exercised in the browser instead and skipped
// here.
// Run:  npm run smoke   (catches missing imports / invalid hooks per page)
import { JSDOM } from 'jsdom';


import { build } from 'esbuild';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.join(here, '..', 'src');
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: 'http://localhost/', pretendToBeVisual: true,
});
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, 'navigator', { value: dom.window.navigator, configurable: true });
global.location = dom.window.location;
global.HTMLElement = dom.window.HTMLElement;
global.Element = dom.window.Element;
global.Node = dom.window.Node;
global.requestAnimationFrame = (cb) => setTimeout(cb, 0);
global.cancelAnimationFrame = clearTimeout;
global.localStorage = dom.window.localStorage;
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
// Charts/graphs need a canvas; in jsdom the context is unavailable, so stub the
// 2D context with a no-op recorder and let the components render their layout.
const ctxStub = new Proxy({ canvas: null, measureText: () => ({ width: 10 }) }, {
  get: (t, k) => (k in t ? t[k] : () => undefined),
  set: () => true,
});
dom.window.HTMLCanvasElement.prototype.getContext = () => ctxStub;

const fixtures = {
  '/health': { status: 'ok', database: 'sqlite', storage: 'local', model: 'test', auth: 'open' },
  '/stats': {
    posts_scanned: 10, alerts: 3, high_risk: 1, unique_identifiers: 5, active_campaigns: 1,
    channels_monitored: 2, alerts_by_category: { drugs: 2 }, alerts_by_status: { new: 1 },
    alerts_over_time: [{ date: '2026-01-01', drugs: 2 }],
    top_channels: [{ channel: 'c', posts: 4, alerts: 1 }],
    source_breakdown: { text: 4 }, media_breakdown: { images: 1, audio: 0, text_only: 9 },
    risk_breakdown: { HIGH: 1, MEDIUM: 1, LOW: 1 },
  },
  '/alerts?page_size=8&sort=risk&order=desc': { total: 0, items: [] },
  '/alerts': {
    total: 1, items: [{
      id: 'a1', post_id: 'p1', category: 'drugs', risk: 'HIGH', confidence: 0.9, status: 'new',
      channel: 'c', timestamp: '2026-01-01T00:00:00Z', text: 'fresh maal dm', source_types: ['text'],
      identifier_types: ['phone'], cluster_id: null, media_type: null,
    }],
  },
  '/campaigns': {
    total: 1, items: [{
      id: 'k1', title: 'Narcotics · 3 channels · 4 ads', category: 'drugs', post_count: 4,
      channels: ['a', 'b', 'c'], shared_identifiers: ['phone:+919000011111'],
      time_span: { start: '', end: '' }, posting_frequency: 1, similarity: 0.8, alert_count: 2,
      snippet: 'fresh maal',
      members: [{ post_id: 'p1', channel: 'a', timestamp: '2026-01-01', text: 'x', media_type: null }],
    }],
  },
  '/identifiers': {
    total: 1, items: [{
      type: 'phone', value: '+919000011111', post_count: 2, channel_count: 3, channels: ['a', 'b', 'c'],
      categories: ['drugs'], first_seen: null, last_seen: null, risk_score: 42, alert_count: 1,
      campaign_count: 1, sources: ['text'],
    }],
  },
  '/metrics': {
    model: {
      version: 'v', labels: ['drugs', 'benign'], macro_f1: 0.9, weighted_f1: 0.9, train_size: 4,
      test_size: 2, calibration: 'x', train_seconds: 1,
      per_class: {
        drugs: { precision: 1, recall: 1, f1: 1, support: 1 },
        benign: { precision: 1, recall: 1, f1: 1, support: 1 },
      },
      confusion_matrix: [[1, 0], [0, 1]],
      hard_negatives: { flagged: 0, tested: 4, examples: [] },
      paraphrase_holdout: { recall: 0.5, examples: [] },
      block_macro_f1: { tfidf_lexicon_augmented: 0.9 },
    },
    latest_run: null,
  },
  '/map': { points: [] },
  '/channels': { channels: ['a'] },
  '/alerts/recent': { items: [] },
  '/realtime/config': { enabled: false },
  '/lexicon': { version: 1, categories: { drugs: { terms: ['x'], slang: [], emojis: [] } }, overlay: [] },
  '/graph': { nodes: [], edges: [], meta: {} },
};

global.fetch = async (url) => {
  const u = new URL(String(url), 'http://localhost');
  const body = fixtures[u.pathname + (u.search || '')] ?? fixtures[u.pathname] ?? {};
  return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) };
};

const define = {
  'process.env.NODE_ENV': '"development"',
  'import.meta.env.DEV': 'false',
  'import.meta.env.VITE_API_URL': '""',
};

const pages = ['Overview', 'Ingest', 'Alerts', 'Campaigns', 'Identifiers', 'Metrics', 'Lexicon'];
const canvasPages = ['EntityGraph', 'MapView'];  // verified in a real browser
let failed = 0;
for (const page of pages) {
  const entry = `
    import React from 'react';
    import { createRoot } from 'react-dom/client';
    import { HashRouter, Routes, Route } from 'react-router-dom';
    import { I18nProvider } from './lib/i18n';
    import Page from './pages/${page}';
    window.__errors = [];
    const orig = console.error;
    console.error = (...a) => { window.__errors.push(a.map(String).join(' ')); };
  window.addEventListener('unhandledrejection', (ev) => { window.__error_event = String(ev.reason); });
    createRoot(document.getElementById('root')).render(
      <I18nProvider><HashRouter><Routes><Route path="*" element={<Page />} /></Routes></HashRouter></I18nProvider>,
    );
  `;
  let code;
  try {
    const res = await build({
      stdin: { contents: entry, resolveDir: src, loader: 'jsx' },
      write: false, bundle: true, format: 'iife', platform: 'browser', jsx: 'automatic',
      define, logLevel: 'silent',
    });
    code = res.outputFiles[0].text;
  } catch (e) {
    failed += 1;
    console.error(`✗ ${page}: bundle failed: ${String(e.message).slice(0, 300)}`);
    continue;
  }
  document.body.innerHTML = '<div id="root"></div>';
  try {
    dom.window.eval(code);
    await new Promise((r) => setTimeout(r, 400));
    // Assert React actually mounted something (not a blank screen).
    const root = document.getElementById('root');
    const mounted = (root.innerHTML || '').length > 50;
    if (!mounted) {
      failed += 1;
      console.error(`✗ ${page}: nothing rendered`);
    } else {
      console.log(`✓ ${page}`);
    }
  } catch (e) {
    // synchronous render errors (missing identifiers, invalid elements, bad hooks)
    failed += 1;
    console.error(`✗ ${page}: ${String(e.message || e).slice(0, 240)}`);
  }
}
for (const page of canvasPages) console.log(`- ${page} (canvas, checked in browser)`);
console.log(failed ? `\n${failed} page(s) failed` : '\nall non-canvas pages render');
process.exit(failed ? 1 : 0);
