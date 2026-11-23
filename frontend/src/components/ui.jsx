import React from 'react';
import { CATEGORY_COLORS, CATEGORY_LABELS, RISK_COLORS, cls, fmtDate } from '../lib/api';
import { useI18n } from '../lib/i18n';

export function CategoryBadge({ category, size = 'sm' }) {
  const { lang } = useI18n();
  const c = CATEGORY_COLORS[category] || '#64748b';
  const label = lang === 'hi' ? undefined : CATEGORY_LABELS[category] || category;
  return (
    <span
      className={cls('inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-medium',
        size === 'xs' ? 'text-[10px]' : 'text-[11px]')}
      style={{ borderColor: `${c}55`, background: `${c}14`, color: c }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: c }} />
      {label || category}
    </span>
  );
}

export function RiskBadge({ risk }) {
  const c = RISK_COLORS[risk] || '#64748b';
  return (
    <span className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide"
      style={{ background: `${c}1a`, color: c, border: `1px solid ${c}44` }}>
      {risk}
    </span>
  );
}

export function StatusBadge({ status }) {
  const { t } = useI18n();
  const map = {
    new: 'border-accent/40 text-accent bg-accent/10',
    under_review: 'border-warn/40 text-warn bg-warn/10',
    confirmed: 'border-ok/40 text-ok bg-ok/10',
    dismissed: 'border-line text-muted bg-raised',
  };
  return <span className={cls('chip', map[status] || map.dismissed)}>{t(`status.${status}`) || status}</span>;
}

export function SourceChip({ source }) {
  const map = {
    text: ['TEXT', 'text-muted'],
    image_ocr: ['OCR', 'text-violet'],
    qr: ['QR', 'text-accent'],
    voice: ['VOICE', 'text-pink'],
  };
  const [label, colour] = map[source] || [String(source).toUpperCase(), 'text-muted'];
  return <span className={cls('chip font-mono text-[10px]', colour)}>{label}</span>;
}

export function Confidence({ value }) {
  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-xs">
      <span className="h-1.5 w-14 overflow-hidden rounded-full bg-line">
        <span className="block h-full rounded-full bg-accent" style={{ width: `${Math.round(value * 100)}%` }} />
      </span>
      {Math.round(value * 100)}%
    </span>
  );
}

export function Kpi({ label, value, sub, accent = 'text-ink', icon }) {
  return (
    <div className="card card-hover p-4">
      <div className="flex items-start justify-between">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">{label}</div>
        {icon}
      </div>
      <div className={cls('mt-2 font-mono text-2xl font-semibold', accent)}>{value}</div>
      {sub && <div className="mt-1 text-[11px] text-muted">{sub}</div>}
    </div>
  );
}

/** Render evidence text with highlighted slang / emoji / intent spans. */
export function HighlightedText({ text, markup, className }) {
  if (!markup || !markup.length) return <p className={cls('whitespace-pre-wrap', className)}>{text}</p>;
  return (
    <p className={cls('whitespace-pre-wrap', className)}>
      {markup.map((m, i) => (m.kind === 'plain'
        ? <React.Fragment key={i}>{m.text}</React.Fragment>
        : <mark key={i} className={cls('hl', `hl-${m.kind}`)} title={m.category || m.kind}>{m.text}</mark>))}
    </p>
  );
}

export function Empty({ children }) {
  return <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-line text-sm text-muted">{children}</div>;
}

export function Drawer({ open, onClose, title, children, footer, width = 'max-w-3xl' }) {
  const { t } = useI18n();
  React.useEffect(() => {
    const h = (e) => e.key === 'Escape' && onClose?.();
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <aside className={cls('flex h-full w-full flex-col border-l border-line bg-panel shadow-2xl', width)}
        onClick={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-ink">{title}</h2>
          <button className="btn px-2 py-1" onClick={onClose} aria-label={t('common.close')}>✕</button>
        </header>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
        {footer && <footer className="border-t border-line p-4">{footer}</footer>}
      </aside>
    </div>
  );
}

export function DateCell({ value }) {
  return <span className="whitespace-nowrap font-mono text-[11px] text-muted">{fmtDate(value)}</span>;
}

export function TooltipBox({ children }) {
  return <div className="rounded-lg border border-line bg-raised px-3 py-2 text-xs shadow-xl">{children}</div>;
}
