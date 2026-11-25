import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, cls, fmtDate, RISK_COLORS } from '../lib/api';
import { useI18n } from '../lib/i18n';
import {
  CategoryBadge, Confidence, Drawer, EmojiSignals, FlagSource, HighlightedText, RiskBadge, Section,
  SourceChip, StatusBadge,
} from './ui';
import IdentityProfile from './IdentityProfile';

const TRANSITIONS = {
  new: ['under_review', 'confirmed', 'dismissed'],
  under_review: ['confirmed', 'dismissed', 'new'],
  confirmed: ['dismissed', 'under_review'],
  dismissed: ['new', 'under_review'],
};

function Row({ label, children }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-line/60 py-1.5 text-[11px] last:border-0">
      <span className="shrink-0 text-muted">{label}</span>
      <span className="min-w-0 break-words text-right text-ink">{children ?? '—'}</span>
    </div>
  );
}

function OcrOverlay({ src, ocr }) {
  const [dims, setDims] = useState({ w: 1, h: 1 });
  if (!src) return null;
  const boxes = ocr?.boxes || [];
  return (
    <div className="relative inline-block max-w-full">
      <img src={src} alt="evidence" className="max-h-96 rounded border border-line object-contain"
        onLoad={(e) => setDims({ w: e.target.naturalWidth || 1, h: e.target.naturalHeight || 1 })} />
      {boxes.map((b, i) => (
        <div key={i} className="absolute rounded-sm border border-accent/70"
          style={{
            left: `${(b.x / dims.w) * 100}%`, top: `${(b.y / dims.h) * 100}%`,
            width: `${(b.w / dims.w) * 100}%`, height: `${(b.h / dims.h) * 100}%`,
            background: 'rgb(var(--c-accent) / 0.08)',
          }}
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
  const [identity, setIdentity] = useState(null);
  const [rescanMsg, setRescanMsg] = useState(null);

  const load = React.useCallback(async () => setA(await api.get(`/alerts/${id}`)), [id]);
  useEffect(() => { load(); setVerify(null); setRescanMsg(null); }, [load]);

  if (!a) return <div className="text-[13px] text-muted">{t('common.loading')}</div>;
  const ex = a.explanation || {};
  const fin = ex.financial || {};
  const fusion = ex.fusion || {};
  const llm = a.llm || ex.llm || {};
  const p = a.post;
  const media = api.mediaUrl(p.media_path);
  const isImage = p.media_type === 'image';
  const isAudio = p.media_type === 'audio';
  const qrList = p.qr?.codes || [];
  const qrUpi = qrList.find((q) => q.kind === 'upi');
  const qr = qrUpi || qrList[0];
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
  const rescan = async () => {
    setBusy(true); setRescanMsg(null);
    try {
      const r = await api.post(`/alerts/${id}/rescan`, {}, { timeout: 180000 });
      setRescanMsg(r);
      await load(); onChanged?.();
    } catch (e) { setRescanMsg({ error: e.message }); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] text-muted">AL-{id.slice(0, 8).toUpperCase()}</span>
        <CategoryBadge category={a.category} />
        <RiskBadge risk={a.risk} />
        <StatusBadge status={a.status} />
        <Confidence value={a.confidence} />
        <FlagSource source={a.flag_source} />
        <span className="chip font-mono">@{p.channel}</span>
        <span className="chip font-mono">{fmtDate(p.timestamp)}</span>
        <button className="btn ml-auto px-2 py-1 text-[11px]" onClick={rescan} disabled={busy}>
          {busy ? 'Working…' : 'Re-scan with GLM'}
        </button>
        <a className="btn px-2 py-1 text-[11px]" href={api.rawUrl(`/report/${id}.pdf`)} target="_blank" rel="noreferrer">
          PDF report
        </a>
      </div>

      {rescanMsg && (
        <div className={cls('border px-3 py-2 text-[11px]', rescanMsg.error ? 'border-danger/30 bg-danger/5 text-danger' : 'border-accent/30 bg-accent/5 text-ink')}>
          {rescanMsg.error || `Verdict refreshed: ${rescanMsg.category} · ${rescanMsg.risk} · ${Math.round((rescanMsg.confidence || 0) * 100)}% · source ${rescanMsg.flag_source}`}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[1.55fr_1fr]">
        <div className="min-w-0 space-y-4">
          <Section
            title="Why this message was flagged"
            right={<FlagSource source={a.flag_source} />}
          >
            {fusion.note && <p className="text-[12px] text-muted">{fusion.note}</p>}
            <div className="mt-2 grid grid-cols-2 gap-2">
              <div className="border border-line bg-base px-2.5 py-2">
                <div className="text-[10px] text-muted">Local engine</div>
                <div className="mt-1 flex items-center gap-2">
                  <span className="text-[12px] font-medium">{fusion.engine?.category || a.category}</span>
                  <span className="font-mono text-[10px] text-muted">{Math.round((fusion.engine?.confidence || 0) * 100)}%</span>
                  <RiskBadge risk={fusion.engine?.risk || 'LOW'} />
                </div>
              </div>
              <div className="border border-line bg-base px-2.5 py-2">
                <div className="flex items-center justify-between text-[10px] text-muted">
                  <span>GLM scanner</span>
                  <span className="font-mono">{llm.model || 'not run'}</span>
                </div>
                {llm.available === false ? (
                  <div className="mt-1 text-[11px] text-muted">Unavailable: {llm.error || 'no verdict'}</div>
                ) : (
                  <div className="mt-1 flex items-center gap-2">
                    <span className="text-[12px] font-medium">{llm.flag ? llm.category : 'benign'}</span>
                    <span className="font-mono text-[10px] text-muted">{Math.round((llm.confidence || 0) * 100)}%</span>
                    <RiskBadge risk={llm.severity || 'LOW'} />
                  </div>
                )}
              </div>
            </div>
            {llm.explanation && <p className="mt-2 text-[12.5px] leading-relaxed text-ink">{llm.explanation}</p>}
            {llm.evidence?.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {llm.evidence.map((e, i) => <span key={i} className="chip text-[10px]">{e}</span>)}
              </div>
            )}
            {(a.emoji || []).length > 0 && (
              <div className="mt-3">
                <div className="text-[10px] font-medium text-muted">Emoji signals read by the detector</div>
                <div className="mt-1.5"><EmojiSignals items={a.emoji} limit={12} showMeaning /></div>
              </div>
            )}
            {ex.reasons?.length > 0 && (
              <ul className="mt-3 space-y-1 border-t border-line pt-2 text-[11px] text-muted">
                {ex.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            )}
          </Section>

          <Section title="Evidence snapshot" right={<span className="text-[10px] text-muted">immutable · SHA-256</span>}>
            <HighlightedText text={p.text} markup={markup} className="text-[13px] leading-relaxed text-ink" />
            {ex.matched_terms?.length > 0 && (
              <div className="mt-3">
                <div className="label">Matched slang / intent</div>
                <div className="flex flex-wrap gap-1">
                  {ex.matched_terms.slice(0, 18).map((m) => <span key={m} className="chip border-danger/30 bg-danger/10 text-danger">{m}</span>)}
                </div>
              </div>
            )}
            <div className="mt-3 grid grid-cols-2 gap-2 border-t border-line pt-2 text-[11px]">
              <div><span className="text-muted">Permalink: </span>
                {p.permalink ? <a className="link break-all" href={p.permalink} target="_blank" rel="noreferrer">{p.permalink}</a> : '—'}</div>
              <div><span className="text-muted">Language: </span>{p.language || '—'}</div>
              <div><span className="text-muted">pHash: </span><span className="font-mono">{p.image_hash || '—'}</span></div>
              <div className="col-span-2"><span className="text-muted">Media SHA-256: </span><span className="font-mono break-all">{p.media_sha256 || '—'}</span></div>
            </div>
          </Section>

          <Section title="Media evidence">
            {isImage && media && (
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-[11px] text-muted">
                  <span>OCR boxes over original</span>
                  <button className="btn px-2 py-0.5 text-[10px]" onClick={() => setShowPre((s) => !s)}>{showPre ? 'Original' : 'Preprocessed'}</button>
                </div>
                {showPre && prePaths.length
                  ? <img src={prePaths[0]} className="max-h-96 rounded border border-line" alt="preprocessed" />
                  : <OcrOverlay src={media} ocr={p.ocr} />}
                {p.ocr?.text && <pre className="max-h-32 overflow-auto rounded border border-line bg-base p-2 text-[11px] text-muted">{p.ocr.text}</pre>}
              </div>
            )}
            {isAudio && media && (
              <div className="space-y-2">
                <audio controls src={media} className="w-full" />
                <div className="text-[11px] text-muted">Transcript · {p.transcript?.language} ({Math.round((p.transcript?.language_probability || 0) * 100)}%) · {p.transcript?.model}</div>
                <div className="space-y-1">
                  {(p.transcript?.segments || []).map((s, i) => (
                    <div key={i} className="flex gap-2 border border-line/60 bg-base px-2 py-1 text-[11px]">
                      <span className="font-mono text-[10px] text-accent">{s.start.toFixed(1)}–{s.end.toFixed(1)}s</span>
                      <span>{s.text}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {!isImage && !isAudio && <p className="text-[12px] text-muted">Text-only evidence (no media attachment).</p>}
          </Section>

          {qr && (
            <Section title="Payment identity from QR code" right={<span className="chip border-accent/40 text-accent">{qr.kind}</span>}>
              <div className="grid grid-cols-2 gap-3 text-[12px]">
                <div>
                  <div className="text-[10px] text-muted">UPI ID / address</div>
                  <div className="mt-0.5 font-mono text-[13px] font-medium text-accent">{qr.parsed?.vpa || qr.parsed?.address || '—'}</div>
                </div>
                <div>
                  <div className="text-[10px] text-muted">Payee name</div>
                  <div className="mt-0.5 text-[13px]">{qr.parsed?.payee_name || '—'}</div>
                </div>
                <div>
                  <div className="text-[10px] text-muted">Amount requested</div>
                  <div className="mt-0.5 font-mono">{qr.parsed?.amount ? `₹${qr.parsed.amount}` : 'open / not fixed'}</div>
                </div>
                <div>
                  <div className="text-[10px] text-muted">Transaction note</div>
                  <div className="mt-0.5 text-[12px]">{qr.parsed?.txn_note || '—'}</div>
                </div>
              </div>
              <div className="mt-2 break-all border border-line bg-base p-2 font-mono text-[10px] text-muted">{qr.data}</div>
              {qr.parsed?.vpa && (
                <button className="btn mt-2 px-2 py-1 text-[11px]" onClick={() => setIdentity(qr.parsed.vpa)}>
                  Resolve this UPI across channels
                </button>
              )}
            </Section>
          )}

          {fin.is_financial && (
            <Section title="Financial ad analysis">
              <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                {[['Type', fin.type], ['Intent', fin.intent], ['Risk', fin.risk], ['Commission', fin.commission || '—']].map(([k, v]) => (
                  <div key={k} className="border border-line bg-base p-2">
                    <div className="text-[10px] text-muted">{k}</div>
                    <div className="mt-0.5 font-mono text-[12px]" style={k === 'Risk' ? { color: RISK_COLORS[v] } : {}}>{v || '—'}</div>
                  </div>
                ))}
              </div>
              <div className="mt-2 flex flex-wrap gap-1">
                {(fin.evidence || []).map((e, i) => (
                  <span key={i} className="chip"><span className="text-muted">{e.kind}</span> {e.phrase}</span>
                ))}
              </div>
            </Section>
          )}

          <Section title={`Identifiers (${a.identifiers.length})`} right={<span className="text-[10px] text-muted">click to resolve</span>}>
            <div className="overflow-x-auto">
              <table className="w-full text-[11.5px]">
                <tbody>
                  {a.identifiers.map((i) => (
                    <tr key={i.type + i.value + i.source} className="border-b border-line/50 last:border-0">
                      <td className="py-1.5 pr-2"><span className="chip font-mono text-[10px]">{i.type}</span></td>
                      <td className="py-1.5 pr-2">
                        {['phone', 'upi', 'wallet', 'username', 'handle'].includes(i.type)
                          ? <button className="link font-mono" onClick={() => setIdentity(i.value)}>{i.value}</button>
                          : <span className="font-mono">{i.value}</span>}
                      </td>
                      <td className="py-1.5 pr-2"><SourceChip source={i.source} /></td>
                      <td className="py-1.5 text-right font-mono text-[10px] text-muted">{Math.round((i.confidence || 0) * 100)}%</td>
                    </tr>
                  ))}
                  {a.identifiers.length === 0 && <tr><td className="py-2 text-muted">No identifiers extracted.</td></tr>}
                </tbody>
              </table>
            </div>
          </Section>
        </div>

        <div className="space-y-4">
          <Section title="Case summary">
            <Row label="Channel"><span className="font-mono">@{p.channel}</span></Row>
            <Row label="Posted">{fmtDate(p.timestamp)}</Row>
            <Row label="External ID"><span className="font-mono">{p.external_id || '—'}</span></Row>
            <Row label="Views"><span className="font-mono">{p.views?.toLocaleString?.() || p.views || '—'}</span></Row>
            <Row label="Platform">{p.platform || 'telegram'}</Row>
            <Row label="Category"><CategoryBadge category={a.category} size="xs" /></Row>
            <Row label="Confidence"><Confidence value={a.confidence} /></Row>
            {ex.scores && (
              <div className="pt-2">
                <div className="mb-1 text-[10px] text-muted">Model posterior</div>
                {Object.entries(ex.scores).slice(0, 5).map(([k, v]) => (
                  <div key={k} className="mb-1 flex items-center gap-2 text-[10px]">
                    <span className="w-24 truncate text-muted">{k}</span>
                    <span className="h-1 flex-1 overflow-hidden rounded bg-line">
                      <span className="block h-full bg-accent/70" style={{ width: `${Math.round(v * 100)}%` }} />
                    </span>
                    <span className="w-7 text-right font-mono text-muted">{v.toFixed(2)}</span>
                  </div>
                ))}
              </div>
            )}
          </Section>

          <Section title="Evidence integrity" right={
            <button className="btn btn-primary px-2 py-0.5 text-[10px]" onClick={doVerify} disabled={busy}>Verify</button>
          }>
            <div className="space-y-1 font-mono text-[10.5px]">
              <div><span className="text-muted">SHA-256: </span><span className="break-all">{a.evidence?.sha256 || '—'}</span></div>
              <div><span className="text-muted">Captured: </span>{fmtDate(a.evidence?.captured_at)}</div>
            </div>
            {verify && (
              <div className={cls('mt-2 border p-2 text-[11px]', verify.ok ? 'border-ok/40 bg-ok/10 text-ok' : 'border-danger/40 bg-danger/10 text-danger')}>
                <div className="font-semibold">{verify.ok ? 'Integrity verified — snapshot unchanged' : 'INTEGRITY MISMATCH'}</div>
                <div className="mt-1 break-all font-mono text-[9px] opacity-80">recomputed: {verify.recomputed_sha256}</div>
              </div>
            )}
          </Section>

          <Section title={t('alerts.workflow')}>
            <div className="flex flex-wrap gap-1.5">
              {TRANSITIONS[a.status]?.map((s) => (
                <button key={s} className={cls('btn px-2 py-1 text-[11px]', s === 'confirmed' && 'btn-primary', s === 'dismissed' && 'btn-danger')}
                  disabled={busy} onClick={() => act(s)}>
                  {t(`status.${s}`)}
                </button>
              ))}
            </div>
            <textarea className="input mt-2 h-16 resize-none" placeholder="Analyst note (logged in the audit trail)…" value={note} onChange={(e) => setNote(e.target.value)} />
            <div className="mt-1.5 flex gap-1.5">
              <button className="btn px-2 py-1 text-[10px]" disabled={!note || busy}
                onClick={async () => { await api.post('/feedback', { alert_id: id, accepted: a.status === 'confirmed', note }); setNote(''); await load(); }}>
                Save feedback
              </button>
              <button className="btn px-2 py-1 text-[10px]" disabled={!note || busy} onClick={() => act(a.status)}>
                Add note
              </button>
            </div>
            <ol className="mt-3 space-y-1 border-l border-line pl-3 text-[10.5px] text-muted">
              {(a.audit || []).map((x) => (
                <li key={x.id}>
                  <span className="font-mono">{fmtDate(x.at)}</span> · <span className="text-ink">{x.actor}</span> · {x.action}
                  {x.from_status && <span> {x.from_status} → {x.to_status}</span>}
                  {x.notes && <span className="text-ink/80"> — {x.notes}</span>}
                </li>
              ))}
              {a.audit.length === 0 && <li>No actions yet.</li>}
            </ol>
          </Section>

          {a.related?.campaign && (
            <div className="border border-violet/30 bg-violet/5 p-3 text-[11px]">
              Part of campaign <Link className="link" to={`/campaigns?focus=${a.related.campaign.id}`}>{a.related.campaign.title}</Link>
              {' '}({a.related.campaign.channels.length} channels)
            </div>
          )}
        </div>
      </div>

      <Drawer open={!!identity} onClose={() => setIdentity(null)} title="Identity profile" width="max-w-3xl">
        {identity && <IdentityProfile value={identity} />}
      </Drawer>
    </div>
  );
}
