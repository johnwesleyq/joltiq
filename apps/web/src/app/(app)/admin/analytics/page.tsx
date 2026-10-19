"use client";
import { useState } from "react";
import { Empty, Loading, PageHead } from "@/components/ui";
import { useApi } from "@/lib/hooks";
type D = { window_days: number; runs: { total: number; finished: number }; verified_resolution_rate: number | null; escalation_rate: number | null;
  approval_latency_seconds_avg: number | null; session_minutes_total: number; sessions: number; cost_total_usd: number; cost_per_verified_resolution_usd: number | null;
  sla_breached_open: number; verifications: Record<string, number>; model_costs: { provider: string; cost_usd: number; tokens: number; calls: number; failures: number }[];
  daily: { date: string; resolved: number; escalated: number; other: number }[] };
const C = { resolved: "#1e8a5a", escalated: "#3b5fa8" };
const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);

function Daily({ data }: { data: D["daily"] }) {
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  if (!data.length) return <Empty>No finished runs in this period.</Empty>;
  const max = Math.max(1, ...data.map((d) => d.resolved + d.escalated));
  const W = 720, H = 180, bw = Math.max(6, Math.min(28, W / data.length - 4));
  return <figure className="panel p-4">
    <figcaption className="mb-2 flex items-center justify-between"><b>Resolved vs escalated, per day</b>
      <span className="flex items-center gap-4 text-sm"><span className="flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: C.resolved }} />Verified resolved</span>
        <span className="flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: C.escalated }} />Escalated</span>
        <button className="underline" onClick={() => setTable(!table)}>{table ? "Show chart" : "Show table"}</button></span></figcaption>
    {table ? <table className="table"><thead><tr><th>Date</th><th>Resolved</th><th>Escalated</th></tr></thead><tbody>{data.map((d) => <tr key={d.date}><td>{d.date}</td><td>{d.resolved}</td><td>{d.escalated}</td></tr>)}</tbody></table> :
    <div className="relative"><svg viewBox={`0 0 ${W} ${H + 20}`} className="w-full" role="img" aria-label="Daily resolved and escalated runs">
      <line x1="0" x2={W} y1={H} y2={H} stroke="var(--color-line)" />
      {data.map((d, i) => { const x = (i + 0.5) * (W / data.length) - bw / 2; const hr = (d.resolved / max) * H; const he = (d.escalated / max) * H;
        return <g key={d.date} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
          <rect x={x - 4} y={0} width={bw + 8} height={H} fill="transparent" />
          {hr > 0 && <rect x={x} y={H - hr} width={bw} height={hr} rx={hr > 4 ? 3 : 0} fill={C.resolved} />}
          {he > 0 && <rect x={x} y={H - hr - he - (hr > 0 ? 2 : 0)} width={bw} height={he} rx={3} fill={C.escalated} />}
        </g>; })}
      <text x="0" y={H + 16} fontSize="11" fill="var(--color-slate)">{data[0]!.date}</text>
      <text x={W} y={H + 16} fontSize="11" fill="var(--color-slate)" textAnchor="end">{data.at(-1)!.date}</text>
    </svg>
    {hover != null && <div className="pointer-events-none absolute top-0 rounded border border-line bg-surface px-2 py-1 text-xs shadow" style={{ left: `${((hover + 0.5) / data.length) * 100}%` }}>
      <b>{data[hover]!.date}</b><br />Resolved {data[hover]!.resolved} · Escalated {data[hover]!.escalated}</div>}</div>}
  </figure>;
}

export default function Analytics() {
  const [days, setDays] = useState(30);
  const r = useApi<D>(["analytics", days], `/analytics/dashboard?days=${days}`);
  return <div><PageHead title="Analytics"><select className="input" value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Period">{[7, 30, 90].map((d) => <option key={d} value={d}>Last {d} days</option>)}</select></PageHead>
    {r.isLoading || !r.data ? <Loading /> : <div className="space-y-6">
      <div className="grid gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-3 lg:grid-cols-6">
        {[["Verified resolution rate", pct(r.data.verified_resolution_rate)], ["Escalation rate", pct(r.data.escalation_rate)], ["Finished runs", String(r.data.runs.finished)],
          ["Avg. approval wait", r.data.approval_latency_seconds_avg == null ? "—" : `${Math.round(r.data.approval_latency_seconds_avg / 60)} min`],
          ["Cost per verified fix", r.data.cost_per_verified_resolution_usd == null ? "—" : `$${r.data.cost_per_verified_resolution_usd.toFixed(2)}`],
          ["Open tickets past SLA", String(r.data.sla_breached_open)]].map(([l, v]) => <div key={l} className="bg-surface p-4"><div className="font-display text-2xl font-bold">{v}</div><div className="text-sm text-slate">{l}</div></div>)}
      </div>
      <Daily data={r.data.daily} />
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="panel p-4"><b>Verification outcomes</b><table className="table mt-2"><tbody>{Object.entries(r.data.verifications).map(([k, v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}
          {!Object.keys(r.data.verifications).length && <tr><td className="text-slate">None yet</td></tr>}</tbody></table></div>
        <div className="panel p-4"><b>Model and tool costs</b> <span className="text-sm text-slate">· ${r.data.cost_total_usd.toFixed(2)} total · {r.data.session_minutes_total} session minutes</span>
          <table className="table mt-2"><thead><tr><th>Provider</th><th>Calls</th><th>Failures</th><th>Tokens</th><th>Cost</th></tr></thead>
            <tbody>{r.data.model_costs.map((c) => <tr key={c.provider}><td>{c.provider}</td><td>{c.calls}</td><td>{c.failures}</td><td>{c.tokens.toLocaleString()}</td><td>${c.cost_usd.toFixed(4)}</td></tr>)}</tbody></table></div>
      </div></div>}
  </div>;
}
