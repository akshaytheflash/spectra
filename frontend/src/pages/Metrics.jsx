import React, { useEffect, useMemo, useState } from 'react';
import { api, CATEGORY_LABELS, cls } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { CategoryBadge, Confidence, HighlightedText, RiskBadge } from '../components/ui';
import { PageHead } from './Overview';

function HeatCell({ value, max }) {
  const t = max ? value / max : 0;
  return (
    <td className="px-2 py-1.5 text-center font-mono text-xs"
      style={{ background: `rgba(34,211,238,${0.06 + 0.5 * t})`, color: t > 0.5 ? '#04121a' : '#e6edf7' }}>
      {value}
    </td>
  );
}

function TestPost() {
  const { t } = useI18n();
  const [text, setText] = useState('Account kiraye pe available, KYC done, ATM + passbook + SIM, daily commission 2500. contact 9000045678');
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try { setResult(await api.post('/classify', { text })); } finally { setBusy(false); }
  };
  return (
    <section className="card p-4">
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">{t('metrics.testPost')}</h3>
      <textarea className="input h-24 font-mono text-xs" value={text} onChange={(e) => setText(e.target.value)} />
      <div className="mt-2 flex items-center gap-2">
        <button className="btn btn-primary" onClick={run} disabled={busy}>{busy ? '…' : 'Classify & explain'}</button>
        <span className="text-[11px] text-muted">Try leetspeak, Devanagari, emoji-only ads or hard negatives.</span>
      </div>
      {result && (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <CategoryBadge category={result.category} />
            <RiskBadge risk={result.risk} />
            <Confidence value={result.confidence} />
            {result.subcategory && <span className="chip text-[10px]">{result.subcategory}</span>}
            <span className={cls('chip text-[10px]', result.is_illicit ? 'border-danger/40 text-danger' : 'border-ok/40 text-ok')}>
              {result.is_illicit ? 'FLAGGED' : 'not flagged'}
            </span>
          </div>
          <HighlightedText text={result.text} markup={result.explanation?.markup} className="rounded-lg border border-line bg-base/50 p-3 text-sm" />
          <div className="grid gap-2 md:grid-cols-2">
            <div>
              <div className="label">Class scores</div>
              {Object.entries(result.scores).map(([k, v]) => (
                <div key={k} className="flex items-center gap-2 text-xs">
                  <span className="w-24 text-muted">{CATEGORY_LABELS[k] || k}</span>
                  <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-line">
                    <span className="block h-full rounded-full bg-accent" style={{ width: `${Math.round(v * 100)}%` }} />
                  </span>
                  <span className="w-10 text-right font-mono text-[10px]">{v.toFixed(2)}</span>
                </div>
              ))}
            </div>
            <div>
              <div className="label">Reasons</div>
              <ul className="space-y-0.5 text-xs text-ink/85">{(result.explanation?.reasons || []).map((r, i) => <li key={i}>• {r}</li>)}</ul>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

export default function Metrics() {
  const { t } = useI18n();
  const [data, setData] = useState(null);
  useEffect(() => { api.get('/metrics').then(setData); }, []);
  const m = data?.model;
  const labels = m?.labels || [];
  const max = useMemo(() => Math.max(1, ...(m?.confusion_matrix || [[]]).flat()), [m]);

  if (!m) return <div className="text-sm text-muted">{t('common.loading')}</div>;

  return (
    <div className="space-y-4">
      <PageHead title={t('metrics.title')} sub={`${m.version} · trained on ${m.train_size} · tested on ${m.test_size} held-out posts (grouped split)`}
        right={<><span className="chip font-mono text-[10px]">{m.calibration}</span><span className="chip font-mono text-[10px]">{m.train_seconds}s train</span></>} />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[['Macro F1', m.macro_f1, 'text-accent'], ['Weighted F1', m.weighted_f1, 'text-ok'],
          ['Hard-negative FPR', m.hard_negatives?.false_positive_rate ?? 0, 'text-violet'],
          ['Paraphrase recall', m.paraphrase_holdout?.recall ?? 0, 'text-pink']].map(([k, v, c]) => (
            <div key={k} className="card p-4">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">{k}</div>
              <div className={cls('mt-1 font-mono text-2xl font-semibold', c)}>{(v * 100).toFixed(1)}%</div>
            </div>
          ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <section className="card p-4">
          <h3 className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-muted">Per-class precision / recall / F1</h3>
          <table className="w-full">
            <thead><tr>{['Class', 'Precision', 'Recall', 'F1', 'Support'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>
              {labels.map((l) => {
                const s = m.per_class[l] || {};
                return (
                  <tr key={l}>
                    <td className="td"><CategoryBadge category={l} /></td>
                    {['precision', 'recall', 'f1'].map((k) => (
                      <td key={k} className="td">
                        <div className="flex items-center gap-2">
                          <span className="h-1.5 w-16 overflow-hidden rounded-full bg-line">
                            <span className="block h-full bg-accent" style={{ width: `${(s[k] || 0) * 100}%` }} />
                          </span>
                          <span className="font-mono text-[11px]">{(s[k] ?? 0).toFixed(3)}</span>
                        </div>
                      </td>
                    ))}
                    <td className="td font-mono text-xs">{s.support}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>

        <section className="card p-4">
          <h3 className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-muted">Confusion matrix (rows = truth)</h3>
          <table className="w-full">
            <thead>
              <tr><th className="th" />{labels.map((l) => <th key={l} className="th text-center text-[10px]">{CATEGORY_LABELS[l]?.split(' ')[0] || l}</th>)}</tr>
            </thead>
            <tbody>
              {(m.confusion_matrix || []).map((row, i) => (
                <tr key={i}>
                  <td className="td text-[10px] text-muted">{CATEGORY_LABELS[labels[i]] || labels[i]}</td>
                  {row.map((v, j) => <HeatCell key={j} value={v} max={max} />)}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-muted">
            <div><span className="text-ink">Block macro-F1:</span> {Object.entries(m.block_macro_f1 || {}).map(([k, v]) => <div key={k} className="font-mono">{k}: {v}</div>)}</div>
            <div><span className="text-ink">Hard negatives:</span> {m.hard_negatives?.flagged}/{m.hard_negatives?.tested} flagged<div className="font-mono">{(m.hard_negatives?.examples || []).length} leak examples</div></div>
          </div>
        </section>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <TestPost />
        <section className="card p-4">
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Unseen-slang holdout (embedding path)</h3>
          <div className="space-y-1 text-xs">
            {(m.paraphrase_holdout?.examples || []).map((e, i) => (
              <div key={i} className="rounded-lg border border-line bg-base/40 p-2">
                <div className="text-ink/85">{e.text}</div>
                <div className="mt-1 flex gap-2 font-mono text-[10px] text-muted">
                  <span>→ {e.predicted}</span><span>{e.confidence}</span>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
