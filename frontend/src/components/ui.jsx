import React from 'react';
import { CATEGORY_COLORS, CATEGORY_LABELS, FLAG_SOURCE_LABELS, RISK_COLORS, cls, fmtDate } from '../lib/api';
import { useI18n } from '../lib/i18n';

/** A category is a colour-coded square + name, not a pill. */
export function CategoryBadge({ category, size = 'sm' }) {
  const { lang } = useI18n();
  const c = CATEGORY_COLORS[category] || CATEGORY_COLORS.benign;
  const label = lang === 'hi' ? category : CATEGORY_LABELS[category] || category;
  return (
    <span className={cls('inline-flex items-center gap-1.5 font-medium text-ink/90', size === 'xs' ? 'text-[11px]' : 'text-[12px]')}>
      <span className="h-2 w-2 shrink-0 rounded-[2px]" style={{ background: c }} />
      {label}
    </span>
  );
}

export function RiskBadge({ risk }) {
  const c = RISK_COLORS[risk] || RISK_COLORS.LOW;
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold" style={{ color: c }}>
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: c }} />
      {(risk || 'low').charAt(0) + (risk || 'low').slice(1).toLowerCase()}
    </span>
  );
}

export function StatusBadge({ status }) {
  const { t } = useI18n();
  const dot = {
    new: 'bg-accent', under_review: 'bg-warn', confirmed: 'bg-ok', dismissed: 'bg-muted',
  }[status] || 'bg-muted';
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-[11px] text-muted">
      <span className={cls('h-1.5 w-1.5 rounded-full', dot)} />
      {t(`status.${status}`) || status}
    </span>
  );
}

export function SourceChip({ source }) {
  const map = {
    text: 'Text', image_ocr: 'OCR', qr: 'QR', voice: 'Voice',
  };
  return <span className="chip font-mono">{map[source] || String(source)}</span>;
}

export function FlagSource({ source }) {
  const colour = {
    llm: 'text-violet border-violet/40',
    'engine+llm': 'text-accent border-accent/40',
    emoji: 'text-brass border-brass/40',
    'llm-veto': 'text-muted',
  }[source] || '';
  return <span className={cls('chip', colour)}>{FLAG_SOURCE_LABELS[source] || source || 'Engine'}</span>;
}

export function EmojiSignals({ items, limit = 6, showMeaning = false }) {
  if (!items?.length) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {items.slice(0, limit).map((e, i) => (
        <span key={`${e.emoji}-${i}`} className="inline-flex items-center gap-1 rounded border border-line bg-raised/60 px-1.5 py-0.5"
          title={`${e.meaning || ''}${e.category ? ` · ${e.category}` : ''}`}>
          <span className="text-[13px] leading-none">{e.emoji}</span>
          {showMeaning && <span className="text-[10px] text-muted">{e.meaning}</span>}
        </span>
      ))}
      {items.length > limit && <span className="font-mono text-[10px] text-muted">+{items.length - limit}</span>}
    </span>
  );
}

export function Confidence({ value }) {
  const v = Math.round((value || 0) * 100);
  return (
    <span className="inline-flex items-center gap-2 font-mono text-[11px] text-muted">
      <span className="h-1 w-12 overflow-hidden rounded-full bg-line">
        <span className="block h-full rounded-full bg-accent" style={{ width: `${v}%` }} />
      </span>
      {v}%
    </span>
  );
}

export function Kpi({ label, value, sub, accent = '', icon }) {
  return (
    <div className="min-w-0 px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[11px] font-medium text-muted">{label}</div>
        {icon}
      </div>
      <div className={cls('mt-1.5 font-mono text-[26px] font-medium leading-none tracking-tight', accent || 'text-ink')}>{value}</div>
      {sub && <div className="mt-1.5 text-[11px] text-muted">{sub}</div>}
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
  return <div className="flex h-32 items-center justify-center border border-dashed border-line text-[13px] text-muted">{children}</div>;
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
    <div className="fixed inset-0 z-50 flex justify-end bg-black/45 backdrop-blur-[2px]" onClick={onClose}>
      <aside className={cls('flex h-full w-full flex-col border-l border-line bg-base shadow-2xl', width)}
        onClick={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between border-b border-line bg-panel px-5 py-3">
          <h2 className="text-[13px] font-semibold text-ink">{title}</h2>
          <button className="btn btn-quiet px-2 py-1" onClick={onClose} aria-label={t('common.close')}>Close</button>
        </header>
        <div className="flex-1 overflow-y-auto p-4 lg:p-5">{children}</div>
        {footer && <footer className="border-t border-line bg-panel p-3">{footer}</footer>}
      </aside>
    </div>
  );
}

export function DateCell({ value }) {
  return <span className="whitespace-nowrap font-mono text-[11px] text-muted">{fmtDate(value)}</span>;
}

export function TooltipBox({ children }) {
  return <div className="rounded-md border border-line bg-raised px-3 py-2 text-xs text-ink shadow-xl">{children}</div>;
}

export function Section({ title, right, children, className }) {
  return (
    <section className={cls('card', className)}>
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <h3 className="section-title">{title}</h3>
        {right}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Field({ label, children, mono }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] font-medium text-muted">{label}</div>
      <div className={cls('mt-0.5 break-words text-[12px] text-ink', mono && 'font-mono')}>{children ?? '—'}</div>
    </div>
  );
}
