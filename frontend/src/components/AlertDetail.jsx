import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, cls, fmtDate, RISK_COLORS } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { CategoryBadge, Confidence, Drawer, HighlightedText, RiskBadge, SourceChip, StatusBadge } from './ui';

const TRANSITIONS = {
  new: ['under_review', 'confirmed', 'dismissed'],
  under_review: ['confirmed', 'dismissed', 'new'],
  confirmed: ['dismissed', 'under_review'],
  dismissed: ['new', 'under_review'],
};

function Section({ title, right, children }) {
  return (
    <section className="card">
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted">{title}</h3>
        {right}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

/** OCR bounding boxes drawn over the original image. */
function OcrOverlay({ src, ocr }) {
  const [dims, setDims] = useState({ w: 1, h: 1 });
  if (!src) return null;
  const boxes = ocr?.boxes || [];
  return (
    <div className="relative inline-block max-w-full">
      <img src={src} alt="evidence" className="max-h-96 rounded-lg border border-line object-contain"
        onLoad={(e) => setDims({ w: e.target.naturalWidth || 1, h: e.target.naturalHeight || 1 })} />
      {boxes.map((b, i) => (
        <div key={i} className="absolute rounded-sm border border-accent/70"
          style={{ left: `${(b.x / dims.w) * 100}%`, top: `${(b.y / dims.h) * 100}%`, width: `${(b.w / dims.w) * 100}%`, height: `${(b.h / dims.h) * 100}%`, background: 'rgba(34,211,238,0.08)' }}
          title={`${b.text} (${(b.confidence * 100).toFixed(0)}%) · ${b.engine}`} />
      ))}
    </div>
  );
}

export function AlertDetail({ id, onChanged }) {
  const { t } = useI18n();
  const [a, setA] = useState(null);
  const [verify, setVerify] = useState(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [showPre, setShowPre] = useState(false);

  const load = React.useCallback(async () => setA(await api.get(`/alerts/${id}`)), [id]);
  useEffect(() => { load(); setVerify(null); }, [load]);

  if (!a) return <div className="text-sm text-muted">{t('common.loading')}</div>;
  const ex = a.explanation || {};
  const fin = ex.financial || {};
  const p = a.post;
  const media = api.mediaUrl(p.media_path);
  const isImage = p.media_type === 'image';
  const isAudio = p.media_type === 'audio';
  const qr = p.qr?.codes?.[0];
  const markup = ex.markup;
  const prePaths = (p.ocr?.preprocessed_paths || []).map((x) => `${api.rawUrl('/media/preprocessed')}//${x.split('/preprocessed/')[1]}`);

  const act = async (status) => {
    setBusy(true);
    try {
      await api.patch(`/alerts/${id}/status`, { status, notes: note || null });
      setNote(''); await load(); onChanged?.();
    } finally { setBusy(false); }
  };
  const doVerify = async () => { setBusy(true); try { setVerify(await api.get(`/alerts/${id}/verify`)); } finally { setBusy(false); } };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] text-muted">AL-{id.slice(0, 8).toUpperCase()}</span>
        <CategoryBadge category={a.category} />
        <RiskBadge risk={a.risk} />
        <StatusBadge status={a.status} />
        <Confidence value={a.confidence} />
        <span className="chip font-mono text-[10px]">@{p.channel}</span>
        <span className="chip font-mono text-[10px]">{fmtDate(p.timestamp)}</span>
        <a className="link ml-auto text-xs" href={api.rawUrl(`/report/${id}.pdf`)} target="_blank" rel="noreferrer">⤓ {t('common.report')}</a>
      </div>

      {ex.reasons && (
        <div className="rounded-lg border border-accent/20 bg-accent/5 p-3">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-accent">Why flagged</div>
          <ul className="space-y-0.5 text-xs text-ink/90">
            {ex.reasons.map((r, i) => <li key={i}>• {r}</li>)}
          </ul>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {Object.entries(ex.scores || {}).map(([k, v]) => (
              <span key={k} className="chip font-mono text-[10px]">{k}: {v.toFixed(2)}</span>
            ))}
          </div>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Evidence snapshot" right={<span className="font-mono text-[10px] text-muted">immutable</span>}>
          <HighlightedText text={p.text} markup={markup} className="text-sm leading-relaxed text-ink" />
          {ex.matched_terms?.length > 0 && (
            <div className="mt-3">
              <div className="label">Matched slang / intent</div>
              <div className="flex flex-wrap gap-1">
                {ex.matched_terms.slice(0, 18).map((m) => <span key={m} className="chip border-danger/30 bg-danger/10 text-[10px] text-pink">{m}</span>)}
              </div>
            </div>
          )}
          <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
            <div><span className="text-muted">Permalink: </span>
              {p.permalink ? <a className="link break-all" href={p.permalink} target="_blank" rel="noreferrer">{p.permalink}</a> : '—'}</div>
            <div><span className="text-muted">Language: </span>{p.language || '—'}</div>
            <div><span className="text-muted">pHash: </span><span className="font-mono">{p.image_hash || '—'}</span></div>
            <div><span className="text-muted">Media SHA-256: </span><span className="font-mono break-all">{p.media_sha256 || '—'}</span></div>
          </div>
        </Section>

        <Section title="Media evidence">
          {isImage && media && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-[11px] text-muted">
                <span>OCR boxes over original</span>
                <button className="btn px-2 py-0.5 text-[10px]" onClick={() => setShowPre((s) => !s)}>{showPre ? 'original' : 'preprocessed'}</button>
              </div>
              {showPre && prePaths.length
                ? <img src={prePaths[0]} className="max-h-96 rounded-lg border border-line" alt="preprocessed" />
                : <OcrOverlay src={media} ocr={p.ocr} />}
              {p.ocr?.text && <pre className="max-h-32 overflow-auto rounded-lg border border-line bg-base/60 p-2 text-[11px] text-muted">{p.ocr.text}</pre>}
            </div>
          )}
          {isAudio && media && (
            <div className="space-y-2">
              <audio controls src={media} className="w-full" />
              <div className="text-[11px] text-muted">Transcript · {p.transcript?.language} ({Math.round((p.transcript?.language_probability || 0) * 100)}%) · {p.transcript?.model}</div>
              <div className="space-y-1">
                {(p.transcript?.segments || []).map((s, i) => (
                  <div key={i} className="flex gap-2 rounded border border-line/60 bg-base/40 px-2 py-1 text-xs">
                    <span className="font-mono text-[10px] text-accent">{s.start.toFixed(1)}–{s.end.toFixed(1)}s</span>
                    <span>{s.text}</span>
                  </div>
                ))}
              </div>
              <pre className="whitespace-pre-wrap rounded-lg border border-line bg-base/60 p-2 text-xs">{p.transcript?.text}</pre>
            </div>
          )}
          {!isImage && !isAudio && <p className="text-xs text-muted">Text-only evidence (no media attachment).</p>}
          {qr && (
            <div className="mt-3 rounded-lg border border-accent/30 bg-accent/5 p-2 text-xs">
              <div className="label mb-1">QR decoded ({qr.kind})</div>
              <div className="font-mono text-[11px] break-all">{qr.data}</div>
              {qr.parsed?.vpa && (
                <div className="mt-1 grid grid-cols-2 gap-1 text-[11px]">
                  <span>VPA: <b className="text-ok">{qr.parsed.vpa}</b></span>
                  <span>Payee: {qr.parsed.payee_name || '—'}</span>
                  <span>Amount: {qr.parsed.amount ? `₹${qr.parsed.amount}` : 'open'}</span>
                  <span>Currency: {qr.parsed.currency}</span>
                </div>
              )}
            </div>
          )}
        </Section>
      </div>

      {fin.is_financial && (
        <Section title="Financial ad analysis">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            {[['Type', fin.type], ['Intent', fin.intent], ['Risk', fin.risk], ['Commission', fin.commission || '—']].map(([k, v]) => (
              <div key={k} className="rounded-lg border border-line bg-base/40 p-2">
                <div className="text-[10px] uppercase tracking-wider text-muted">{k}</div>
                <div className="font-mono text-sm" style={k === 'Risk' ? { color: RISK_COLORS[v] } : {}}>{v || '—'}</div>
              </div>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap gap-1">
            {(fin.evidence || []).map((e, i) => (
              <span key={i} className="chip text-[10px]"><span className="text-muted">{e.kind}</span> {e.phrase}</span>
            ))}
          </div>
        </Section>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title={`Identifiers (${a.identifiers.length})`}>
          <table className="w-full text-xs">
            <tbody>
              {a.identifiers.map((i) => (
                <tr key={i.type + i.value + i.source} className="border-b border-line/50 last:border-0">
                  <td className="py-1 pr-2"><span className="chip font-mono text-[10px]">{i.type}</span></td>
                  <td className="py-1 pr-2">
                    {['phone', 'upi', 'wallet', 'username'].includes(i.type)
                      ? <Link className="link font-mono" to={`/graph?identifier=${encodeURIComponent(i.value)}`}>{i.value}</Link>
                      : <span className="font-mono">{i.value}</span>}
                  </td>
                  <td className="py-1 pr-2"><SourceChip source={i.source} /></td>
                  <td className="py-1 text-right font-mono text-[10px] text-muted">{Math.round((i.confidence || 0) * 100)}%</td>
                </tr>
              ))}
              {a.identifiers.length === 0 && <tr><td className="text-muted">none</td></tr>}
            </tbody>
          </table>
        </Section>

        <Section title="Evidence integrity" right={
          <button className="btn btn-primary px-2 py-1 text-[11px]" onClick={doVerify} disabled={busy}>{t('common.verify')}</button>
        }>
          <div className="space-y-1 font-mono text-[11px]">
            <div><span className="text-muted">SHA-256: </span><span className="break-all">{a.evidence?.sha256 || '—'}</span></div>
            <div><span className="text-muted">Captured: </span>{fmtDate(a.evidence?.captured_at)}</div>
          </div>
          {verify && (
            <div className={cls('mt-2 rounded-lg border p-2 text-xs', verify.ok ? 'border-ok/40 bg-ok/10 text-ok' : 'border-danger/40 bg-danger/10 text-danger')}>
              <div className="font-semibold">{verify.ok ? '✓ Integrity verified — snapshot unchanged' : '✗ INTEGRITY MISMATCH'}</div>
              <div className="mt-1 break-all font-mono text-[10px] opacity-80">recomputed: {verify.recomputed_sha256}</div>
            </div>
          )}
        </Section>
      </div>

      <Section title={t('alerts.workflow')}>
        <div className="flex flex-wrap items-center gap-2">
          {TRANSITIONS[a.status]?.map((s) => (
            <button key={s} className={cls('btn text-xs', s === 'confirmed' && 'btn-primary', s === 'dismissed' && 'btn-danger')}
              disabled={busy} onClick={() => act(s)}>
              → {t(`status.${s}`)}
            </button>
          ))}
          <input className="input ml-auto max-w-sm" placeholder="Analyst note (logged in audit trail)…" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="btn text-xs" disabled={!note || busy}
            onClick={async () => { await api.post('/feedback', { alert_id: id, accepted: a.status === 'confirmed', note }); setNote(''); await load(); }}>
            Save feedback
          </button>
        </div>
        <ol className="mt-3 space-y-1 border-l border-line pl-3 text-xs">
          {(a.audit || []).map((x) => (
            <li key={x.id} className="text-muted">
              <span className="font-mono text-[10px]">{fmtDate(x.at)}</span> · <span className="text-ink">{x.actor}</span> · {x.action}
              {x.from_status && <span> {x.from_status} → {x.to_status}</span>}
              {x.notes && <span className="text-ink/80"> — {x.notes}</span>}
            </li>
          ))}
          {a.audit.length === 0 && <li className="text-muted">No actions yet.</li>}
        </ol>
      </Section>

      {a.related?.campaign && (
        <div className="rounded-lg border border-violet/30 bg-violet/5 p-3 text-xs">
          Part of campaign <Link className="link" to={`/campaigns?focus=${a.related.campaign.id}`}>{a.related.campaign.title}</Link>
          {' '}({a.related.campaign.channels.length} channels)
        </div>
      )}
    </div>
  );
}
