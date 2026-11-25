import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, fmtDate } from '../lib/api';
import { CategoryBadge, RiskBadge } from './ui';

function Bucket({ label, values, mono = true }) {
  if (!values?.length) return null;
  return (
    <div>
      <div className="text-[10px] font-medium text-muted">{label}</div>
      <div className="mt-1 flex flex-wrap gap-1">
        {values.map((v) => (
          <span key={v} className={mono ? 'chip font-mono text-[10px]' : 'chip text-[10px]'}>{v}</span>
        ))}
      </div>
    </div>
  );
}

export default function IdentityProfile({ value }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    setData(null); setErr(null);
    api.get(`/identity/${encodeURIComponent(value)}`).then(setData).catch((e) => setErr(e.message));
  }, [value]);

  if (err) return <div className="text-[13px] text-danger">{err}</div>;
  if (!data) return <div className="text-[13px] text-muted">Resolving identifier…</div>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="chip font-mono text-[11px]">{data.type}</span>
        <span className="font-mono text-[15px] font-medium text-ink">{data.value}</span>
        <span className="ml-auto text-[11px] text-muted">risk score</span>
        <span className="font-mono text-[15px] font-medium text-danger">{data.risk_score}</span>
      </div>

      <div className="grid grid-cols-2 gap-3 border-y border-line py-3 text-[11px] sm:grid-cols-4">
        <div><div className="text-muted">Channels</div><div className="mt-0.5 font-mono text-[13px]">{data.channels.length}</div></div>
        <div><div className="text-muted">Posts</div><div className="mt-0.5 font-mono text-[13px]">{data.posts.length}</div></div>
        <div><div className="text-muted">Alerts</div><div className="mt-0.5 font-mono text-[13px]">{data.alerts.length}</div></div>
        <div><div className="text-muted">First seen</div><div className="mt-0.5 font-mono text-[11px]">{data.first_seen ? fmtDate(data.first_seen) : '—'}</div></div>
      </div>

      <div className="space-y-3">
        <Bucket label="UPI IDs" values={data.upis} />
        <Bucket label="Phone numbers" values={data.phones} />
        <Bucket label="Wallets" values={data.wallets} />
        <Bucket label="Handles" values={data.handles} />
        <Bucket label="Payee names from QR codes" values={data.payee_names} mono={false} />
        <Bucket label="Channels" values={data.channels.map((c) => `@${c}`)} />
        <Bucket label="Aliases / raw forms" values={data.aliases} />
      </div>

      <div>
        <div className="mb-1 text-[11px] font-semibold text-ink">Linked messages</div>
        <div className="max-h-72 overflow-y-auto border border-line">
          {data.posts.map((p) => (
            <div key={p.id} className="border-b border-line/60 px-3 py-2 last:border-0">
              <div className="flex items-center gap-2 text-[10px] text-muted">
                {p.category && <CategoryBadge category={p.category} size="xs" />}
                {p.risk && <RiskBadge risk={p.risk} />}
                <span className="font-mono">@{p.channel}</span>
                <span className="ml-auto font-mono">{fmtDate(p.timestamp)}</span>
              </div>
              <div className="mt-1 line-clamp-2 text-[11.5px] text-ink/90">{p.text}</div>
              <div className="mt-1 flex gap-2 text-[10px]">
                {p.alert_id && <Link className="link" to={`/alerts?focus=${p.alert_id}`}>alert record</Link>}
                {p.permalink && <a className="link" href={p.permalink} target="_blank" rel="noreferrer">telegram</a>}
              </div>
            </div>
          ))}
          {data.posts.length === 0 && <div className="p-3 text-[12px] text-muted">No linked messages.</div>}
        </div>
      </div>
    </div>
  );
}
