import React, { useEffect, useMemo, useState } from 'react';
import { api, CATEGORY_COLORS, CATEGORY_LABELS, cls } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { CategoryBadge, Empty, HighlightedText } from '../components/ui';
import { PageHead } from './Overview';

const KINDS = ['terms', 'slang', 'devanagari', 'emojis', 'intent'];

export default function Lexicon() {
  const { t } = useI18n();
  const [data, setData] = useState(null);
  const [tab, setTab] = useState('drugs');
  const [kind, setKind] = useState('slang');
  const [term, setTerm] = useState('');
  const [weight, setWeight] = useState(1.0);
  const [probe, setProbe] = useState('Fresh m@al landed, discreet packing, Noida delivery — dm @newplug_99');
  const [result, setResult] = useState(null);
  const [msg, setMsg] = useState('');

  const load = () => api.get('/lexicon').then(setData);
  useEffect(() => { load(); }, []);

  const overlayByCat = useMemo(() => {
    const m = {};
    (data?.overlay || []).forEach((o) => { (m[o.category] ||= []).push(o); });
    return m;
  }, [data]);

  const add = async () => {
    if (!term.trim()) return;
    await api.post('/lexicon/terms', { category: tab, term: term.trim(), kind, weight: Number(weight) });
    setMsg(`Added “${term}” to ${CATEGORY_LABELS[tab]} (${kind}) — engine reloaded, no redeploy.`);
    setTerm('');
    await load();
  };
  const remove = async (id) => { await api.del(`/lexicon/terms/${id}`); await load(); };

  const test = async () => { setResult(await api.post('/classify', { text: probe })); };

  if (!data) return <div className="text-sm text-muted">{t('common.loading')}</div>;

  return (
    <div className="space-y-4">
      <PageHead title={t('lexicon.title')}
        sub={`lexicon v${data.version} · YAML base + ${data.overlay.length} analyst terms (live reload)`} />

      <div className="grid gap-4 xl:grid-cols-3">
        <section className="card p-4 xl:col-span-2">
          <div className="mb-3 flex flex-wrap gap-1">
            {Object.keys(CATEGORY_LABELS).filter((c) => data.categories[c]).map((c) => (
              <button key={c} onClick={() => setTab(c)}
                className={cls('rounded-md border px-2 py-1 text-[11px]', tab === c ? 'border-accent/50 bg-accent/10 text-accent' : 'border-line text-muted hover:text-ink')}>
                {CATEGORY_LABELS[c]}
              </button>
            ))}
          </div>
          <div className="space-y-2">
            {KINDS.map((k) => {
              const items = data.categories[tab]?.[k] || [];
              if (!items.length) return null;
              return (
                <div key={k}>
                  <div className="label">{k} <span className="text-muted/60">({items.length})</span></div>
                  <div className="flex flex-wrap gap-1">
                    {items.map((x) => <span key={String(x)} className="chip text-[10px]" style={{ borderColor: `${CATEGORY_COLORS[tab]}44` }}>{String(x)}</span>)}
                  </div>
                </div>
              );
            })}
          </div>
          <div className="mt-4 border-t border-line pt-3">
            <div className="label">Analyst overlay for {CATEGORY_LABELS[tab]}</div>
            <div className="flex flex-wrap gap-1">
              {(overlayByCat[tab] || []).map((o) => (
                <span key={o.id} className="chip border-ok/40 bg-ok/10 text-[10px] text-ok">
                  {o.kind}: {o.term}
                  <button className="ml-1 text-danger" onClick={() => remove(o.id)}>✕</button>
                </span>
              ))}
              {!(overlayByCat[tab] || []).length && <span className="text-xs text-muted">none yet</span>}
            </div>
          </div>
        </section>

        <section className="card space-y-2 p-4">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted">Add term (no redeploy)</h3>
          <input className="input" placeholder="slang / emoji / intent phrase" value={term} onChange={(e) => setTerm(e.target.value)} />
          <div className="grid grid-cols-2 gap-2">
            <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
              {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
            <input className="input" type="number" step="0.1" min="0.1" max="3" value={weight} onChange={(e) => setWeight(e.target.value)} />
          </div>
          <button className="btn btn-primary w-full" onClick={add}>Add to {CATEGORY_LABELS[tab]}</button>
          {msg && <p className="text-[11px] text-ok">{msg}</p>}
          <div className="pt-2">
            <div className="label">Probe with current lexicon</div>
            <textarea className="input h-24 font-mono text-xs" value={probe} onChange={(e) => setProbe(e.target.value)} />
            <button className="btn mt-2 w-full" onClick={test}>Classify</button>
          </div>
          {result && (
            <div className="space-y-2">
              <div className="flex items-center gap-2"><CategoryBadge category={result.category} /><span className="font-mono text-[11px]">{Math.round(result.confidence * 100)}%</span></div>
              <HighlightedText text={result.text} markup={result.explanation?.markup} className="text-xs" />
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
