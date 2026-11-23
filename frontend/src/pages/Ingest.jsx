import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, cls, fmtDate } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { CategoryBadge, Confidence, DateCell, HighlightedText, RiskBadge, StatusBadge } from '../components/ui';
import { PageHead } from './Overview';

const TABS = [
  { key: 'telegram', label: 'Telegram links' },
  { key: 'upload', label: 'Upload data' },
  { key: 'single', label: 'Single item' },
  { key: 'search', label: 'Search' },
];

function JobProgress({ job, onDone }) {
  const [j, setJ] = useState(job);
  useEffect(() => {
    setJ(job);
    if (!job || ['completed', 'failed'].includes(job.status)) { onDone?.(job); return undefined; }
    const id = setInterval(async () => {
      try {
        const next = await api.get(`/ingest/jobs/${job.id}`);
        setJ(next);
        if (['completed', 'failed'].includes(next.status)) { clearInterval(id); onDone?.(next); }
      } catch { /* ignore */ }
    }, 1500);
    return () => clearInterval(id);
  }, [job?.id, job?.status]);
  if (!j) return null;
  const pct = j.total ? Math.min(100, Math.round((j.done / j.total) * 100)) : (j.status === 'completed' ? 100 : 8);
  const color = j.status === 'failed' ? 'bg-danger' : j.status === 'completed' ? 'bg-ok' : 'bg-accent';
  return (
    <div className="mt-3 rounded-lg border border-line bg-base/50 p-3">
      <div className="flex items-center justify-between text-[11px]">
        <span className="font-mono text-muted">{j.kind} · {j.status}</span>
        <span className="font-mono">{j.done}/{j.total || '?'} · {pct}%</span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-line">
        <div className={cls('h-full rounded-full transition-all', color, j.status === 'running' && 'animate-pulse')} style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-1 text-[11px] text-muted">{j.message}</div>
      {j.result?.error && <div className="mt-1 text-[11px] text-danger">{j.result.error}</div>}
      {j.status === 'completed' && j.result?.posts !== undefined && (
        <div className="mt-1 font-mono text-[11px] text-ok">
          {j.result.posts} posts analysed · {j.result.alerts} alerts
          {j.result.mode && ` · ${j.result.mode} mode`}
        </div>
      )}
      {j.result?.channels?.length > 0 && (
        <ul className="mt-1 space-y-0.5 text-[10px] text-muted">
          {j.result.channels.map((c, i) => (
            <li key={i}>@{c.channel}: fetched {c.fetched}, media {c.with_media}{c.error ? ` — ${c.error}` : ''}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TelegramTab() {
  const [targets, setTargets] = useState('https://t.me/durov\n@telegram');
  const [limit, setLimit] = useState(30);
  const [job, setJob] = useState(null);
  const [err, setErr] = useState(null);
  const run = async () => {
    setErr(null);
    try {
      const list = targets.split(/[\s,]+/).filter(Boolean);
      setJob(await api.post('/ingest/telegram', { targets: list, limit, run_pipeline: true, do_ocr: true, ocr_passes: 1 }, { timeout: 600000 }));
    } catch (e) { setErr(e.message); }
  };
  return (
    <section className="card p-4">
      <h3 className="text-sm font-semibold text-ink">Analyse public Telegram channels</h3>
      <p className="mt-1 text-[11px] text-muted">Paste public t.me links, t.me/channel/123 post links or @usernames (one per line). Private invites are rejected. Read-only, last N messages.</p>
      <textarea className="input mt-3 h-24 font-mono text-xs" value={targets} onChange={(e) => setTargets(e.target.value)} />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <label className="text-[11px] text-muted">Last N messages</label>
        <input className="input w-24" type="number" min={1} max={500} value={limit} onChange={(e) => setLimit(Number(e.target.value))} />
        <button className="btn btn-primary ml-auto" onClick={run}>Verify &amp; analyse</button>
      </div>
      {err && <p className="mt-2 text-xs text-danger">{err}</p>}
      <JobProgress job={job} />
      <div className="mt-3 grid grid-cols-2 gap-2 text-[10px] text-muted md:grid-cols-4">
        {['scope: public only', 'collector: Telethon read-only', 'rate-limit: 0.35s/msg', 'resumable offset_id'].map((x) => <span key={x} className="chip">{x}</span>)}
      </div>
    </section>
  );
}

function UploadTab() {
  const [file, setFile] = useState(null);
  const [headers, setHeaders] = useState([]);
  const [mapping, setMapping] = useState({});
  const [job, setJob] = useState(null);
  const [drag, setDrag] = useState(false);
  const [err, setErr] = useState(null);
  const inputRef = useRef(null);
  const CANON = ['post_id', 'platform', 'channel', 'timestamp', 'text', 'image_path', 'audio_path', 'label'];

  const inspect = async (f) => {
    setFile(f); setErr(null); setHeaders([]); setMapping({});
    if (/\.(csv|json|jsonl|ndjson)$/i.test(f.name)) {
      const text = await f.text();
      try {
        if (/\.json/i.test(f.name)) {
          const arr = JSON.parse(text);
          const row = Array.isArray(arr) ? arr[0] : arr.posts?.[0] || arr;
          setHeaders(Object.keys(row || {}));
        } else {
          const first = text.split(/\r?\n/)[0];
          setHeaders(first.split(/[,;\t|]/).map((h) => h.replace(/"/g, '').trim()));
        }
      } catch { setHeaders([]); }
    }
  };
  const send = async () => {
    if (!file) return;
    setErr(null);
    const fd = new FormData();
    fd.append('file', file);
    if (Object.keys(mapping).length) fd.append('mapping', JSON.stringify(mapping));
    fd.append('do_ocr', 'true');
    try { setJob(await api.upload('/ingest/file', fd, { timeout: 600000 })); } catch (e) { setErr(e.message); }
  };
  return (
    <section className="card p-4">
      <h3 className="text-sm font-semibold text-ink">Upload CSV / JSON / ZIP</h3>
      <p className="mt-1 text-[11px] text-muted">Posts with text, image paths, audio paths and optional ground-truth labels. ZIPs may include the media folder.</p>
      <div
        className={cls('mt-3 flex h-32 cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed text-sm transition-colors',
          drag ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:border-accent/40')}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files[0]) inspect(e.dataTransfer.files[0]); }}
        onClick={() => inputRef.current?.click()}
      >
        <span className="text-2xl">⇪</span>
        <span className="mt-1">{file ? file.name : 'Drop a file or click to browse'}</span>
        {file && <span className="font-mono text-[10px]">{(file.size / 1024).toFixed(0)} KB</span>}
      </div>
      <input ref={inputRef} type="file" className="hidden" accept=".csv,.json,.jsonl,.zip" onChange={(e) => e.target.files[0] && inspect(e.target.files[0])} />
      {headers.length > 0 && (
        <div className="mt-3">
          <div className="label">Column mapping</div>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            {CANON.map((c) => (
              <div key={c}>
                <div className="text-[10px] text-muted">{c}</div>
                <select className="input text-xs" value={mapping[c] || ''} onChange={(e) => setMapping((m) => ({ ...m, [c]: e.target.value }))}>
                  <option value="">—</option>
                  {headers.map((h) => <option key={h} value={h}>{h}</option>)}
                </select>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="mt-3 flex items-center gap-2">
        <button className="btn btn-primary" onClick={send} disabled={!file}>Upload &amp; analyse</button>
        <span className="text-[11px] text-muted">Auto-detects the organizers&apos; columns (post_id, platform, channel, timestamp, text, image_path, audio_path, label).</span>
      </div>
      {err && <p className="mt-2 text-xs text-danger">{err}</p>}
      <JobProgress job={job} />
    </section>
  );
}

function SingleTab() {
  const [mode, setMode] = useState('text');
  const [text, setText] = useState('');
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [preview, setPreview] = useState(null);

  const runText = async () => {
    setBusy(true); setErr(null);
    try { setRes({ kind: 'text', data: await api.post('/classify', { text }) }); } finally { setBusy(false); }
  };
  const runFile = async (file) => {
    setBusy(true); setErr(null); setRes(null);
    setPreview(file.type.startsWith('image') ? URL.createObjectURL(file) : null);
    const fd = new FormData(); fd.append('file', file);
    try {
      const isAudio = file.type.startsWith('audio') || /\.(ogg|opus|mp3|m4a|wav|aac|flac|webm)$/i.test(file.name);
      const data = await api.upload(isAudio ? '/analyze/audio' : '/analyze/image', fd);
      setRes({ kind: isAudio ? 'audio' : 'image', data });
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const cls_ = res?.data?.classification || res?.data;
  const idents = res?.data?.identifiers || cls_?.identifiers || [];
  return (
    <section className="card p-4">
      <h3 className="text-sm font-semibold text-ink">Single-item analysis</h3>
      <div className="mt-2 flex gap-1">
        {[['text', 'Paste text'], ['image', 'Image / QR'], ['audio', 'Voice note']].map(([k, l]) => (
          <button key={k} className={cls('btn text-xs', mode === k && 'btn-primary')} onClick={() => { setMode(k); setRes(null); }}>{l}</button>
        ))}
      </div>
      {mode === 'text' && (
        <>
          <textarea className="input mt-3 h-28 font-mono text-xs" placeholder="Paste a public post…" value={text} onChange={(e) => setText(e.target.value)} />
          <button className="btn btn-primary mt-2" onClick={runText} disabled={busy || !text}>Analyse</button>
        </>
      )}
      {mode !== 'text' && (
        <label className="mt-3 flex h-28 cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-line text-sm text-muted hover:border-accent/40">
          <span>⇪ choose {mode === 'image' ? 'poster / screenshot / QR' : 'voice note (ogg, opus, mp3, m4a, wav)'}</span>
          <input type="file" className="hidden" accept={mode === 'image' ? 'image/*' : 'audio/*,.ogg,.opus'} onChange={(e) => e.target.files[0] && runFile(e.target.files[0])} />
        </label>
      )}
      {busy && <p className="mt-2 text-xs text-accent">analysing{mode === 'image' ? ' (OCR multi-pass)' : mode === 'audio' ? ' (transcribing)' : ''}…</p>}
      {err && <p className="mt-2 text-xs text-danger">{err}</p>}
      {preview && <img src={preview} className="mt-3 max-h-56 rounded-lg border border-line" alt="upload" />}
      {res?.kind === 'audio' && res.data.transcript && (
        <div className="mt-3 rounded-lg border border-line bg-base/50 p-2 text-xs">
          <div className="label">Transcript · {res.data.transcript.language}</div>
          <p>{res.data.transcript.text}</p>
          {(res.data.transcript.segments || []).map((s, i) => (
            <div key={i} className="mt-1 font-mono text-[10px] text-muted">{s.start.toFixed(1)}–{s.end.toFixed(1)}s · {s.text}</div>
          ))}
        </div>
      )}
      {res?.kind === 'image' && res.data.ocr && (
        <div className="mt-3 rounded-lg border border-line bg-base/50 p-2 text-xs">
          <div className="label">OCR · {res.data.ocr.boxes?.length || 0} boxes · passes {res.data.ocr.passes?.join(', ')}</div>
          <pre className="whitespace-pre-wrap text-[11px] text-ink/85">{res.data.ocr.text}</pre>
          {(res.data.ocr.qr_codes || []).map((q, i) => (
            <div key={i} className="mt-1 rounded border border-accent/30 bg-accent/5 p-1 font-mono text-[10px]">
              QR [{q.kind}] {q.data}
              {q.parsed?.vpa && <span className="ml-2 text-ok">VPA {q.parsed.vpa} · {q.parsed.payee_name} {q.parsed.amount ? `· ₹${q.parsed.amount}` : ''}</span>}
            </div>
          ))}
        </div>
      )}
      {cls_ && (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <CategoryBadge category={cls_.category} /><RiskBadge risk={cls_.risk} /><Confidence value={cls_.confidence} />
            {cls_.subcategory && <span className="chip text-[10px]">{cls_.subcategory}</span>}
          </div>
          {cls_.text && <HighlightedText text={cls_.text} markup={cls_.explanation?.markup} className="rounded-lg border border-line bg-base/50 p-2 text-xs" />}
          <div className="flex flex-wrap gap-1">
            {idents.map((i, k) => (
              <a key={k} className="chip font-mono text-[10px] text-accent" href={`/graph?identifier=${encodeURIComponent(i.value)}`}>{i.type}: {i.value}</a>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function SearchTab() {
  const [q, setQ] = useState('');
  const [mode, setMode] = useState('auto');
  const [filters, setFilters] = useState({ channel: '', category: '', source: '', date_from: '', date_to: '' });
  const [channels, setChannels] = useState([]);
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get('/channels').then((r) => setChannels(r.channels)).catch(() => {}); }, []);
  const run = useCallback(async () => {
    setBusy(true);
    try {
      const qs = new URLSearchParams({ q, mode });
      Object.entries(filters).forEach(([k, v]) => { if (v) qs.set(k, v); });
      setRes(await api.get(`/search?${qs}`));
    } finally { setBusy(false); }
  }, [q, mode, filters]);
  return (
    <section className="card p-4">
      <h3 className="text-sm font-semibold text-ink">Search collected data</h3>
      <div className="mt-2 flex flex-wrap gap-2">
        <input className="input min-w-[240px] flex-1" placeholder="phone, UPI, @handle, slang, free text…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && run()} />
        <select className="input w-36" value={mode} onChange={(e) => setMode(e.target.value)}>
          {['auto', 'hybrid', 'identifier', 'semantic', 'text'].map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <button className="btn btn-primary" onClick={run} disabled={busy || !q}>{busy ? '…' : 'Search'}</button>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-5">
        <select className="input" value={filters.channel} onChange={(e) => setFilters((f) => ({ ...f, channel: e.target.value }))}>
          <option value="">channel: all</option>{channels.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select className="input" value={filters.category} onChange={(e) => setFilters((f) => ({ ...f, category: e.target.value }))}>
          {['', 'drugs', 'arms', 'fake_docs', 'mule_account', 'atm_card', 'sim_card', 'net_banking_kit', 'benign'].map((c) => <option key={c} value={c}>{c || 'category: all'}</option>)}
        </select>
        <select className="input" value={filters.source} onChange={(e) => setFilters((f) => ({ ...f, source: e.target.value }))}>
          {['', 'text', 'image_ocr', 'qr', 'voice'].map((c) => <option key={c} value={c}>{c || 'source: all'}</option>)}
        </select>
        <input type="date" className="input" value={filters.date_from} onChange={(e) => setFilters((f) => ({ ...f, date_from: e.target.value }))} />
        <input type="date" className="input" value={filters.date_to} onChange={(e) => setFilters((f) => ({ ...f, date_to: e.target.value }))} />
      </div>
      {res && (
        <div className="mt-3">
          <div className="mb-1 text-[11px] text-muted">{res.total} results · mode {res.mode}</div>
          <ul className="divide-y divide-line/60">
            {res.hits.map((h) => (
              <li key={h.post_id} className="py-2">
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  <span className="chip font-mono text-[10px]">{h.score.toFixed(2)}</span>
                  {h.category && <CategoryBadge category={h.category} size="xs" />}
                  <span className="text-muted">@{h.channel}</span>
                  <DateCell value={h.timestamp} />
                  {h.alert_id && <a className="link ml-auto text-[11px]" href={`/alerts?focus=${h.alert_id}`}>open alert →</a>}
                </div>
                <p className="mt-1 line-clamp-2 text-xs text-ink/85">{h.text}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export default function Ingest() {
  const { t } = useI18n();
  const [tab, setTab] = useState('telegram');
  return (
    <div className="space-y-4">
      <PageHead title={t('ingest.title')} sub="Central workspace: Telegram links, uploads, instant analysis and search" />
      <div className="flex flex-wrap gap-1">
        {TABS.map((x) => (
          <button key={x.key} className={cls('btn text-xs', tab === x.key && 'btn-primary')} onClick={() => setTab(x.key)}>{x.label}</button>
        ))}
      </div>
      {tab === 'telegram' && <TelegramTab />}
      {tab === 'upload' && <UploadTab />}
      {tab === 'single' && <SingleTab />}
      {tab === 'search' && <SearchTab />}
    </div>
  );
}
