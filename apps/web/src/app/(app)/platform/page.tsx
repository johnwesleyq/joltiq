"use client";
import { Badge, ErrorNote, Loading, PageHead } from "@/components/ui";
import { api, fmtDate, type Page } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";
type T = { id: string; name: string; status: string; plan: string; sub_status: string | null; ai_cost_30d: number; created_at: string };
type H = { workers: { worker_id: string; kind: string; last_beat: string; healthy: boolean }[]; queue: Record<string, number>; browser_worker: { ok: boolean; sessions?: number };
  dead_letters: { id: string; kind: string; attempts: number; error: string; at: string }[]; users: number; research: { configured: boolean; provider: string | null } };
type S = { id: string; kind: string; severity: string; ip: string | null; at: string };
export default function Platform() {
  const t = useApi<Page<T>>(["platform", "tenants"], "/admin/platform/tenants?page_size=100");
  const h = useApi<H>(["platform", "health"], "/admin/platform/health", { refetchInterval: 10000 });
  const s = useApi<Page<S>>(["platform", "sec"], "/admin/platform/security-events?page_size=25");
  const patch = useAct((v: { id: string; body: object }) => api(`/admin/platform/tenants/${v.id}`, { method: "PATCH", json: v.body }), [["platform"]]);
  const requeue = useAct((id: string) => api(`/admin/platform/jobs/${id}/requeue`, { method: "POST" }), [["platform"]]);
  return <div className="space-y-10"><PageHead title="Platform" />
    <ErrorNote error={t.error || patch.error} />
    {h.data && <section className="grid gap-6 md:grid-cols-3">
      <div className="panel p-4"><b>Workers</b><ul className="mt-2 text-sm">{h.data.workers.map((w) => <li key={w.worker_id}><Badge value={w.healthy ? "VERIFIED" : "FAILED"} label={w.healthy ? "up" : "stale"} /> {w.kind} <span className="text-slate">{fmtDate(w.last_beat)}</span></li>)}
        {!h.data.workers.length && <li className="text-stop">No job workers running.</li>}<li className="mt-1">Browser worker: {h.data.browser_worker.ok ? `up (${h.data.browser_worker.sessions} sessions)` : <span className="text-stop">down</span>}</li></ul></div>
      <div className="panel p-4"><b>Queue</b><ul className="mt-2 text-sm">{Object.entries(h.data.queue).map(([k, v]) => <li key={k}>{k}: {v}</li>)}</ul></div>
      <div className="panel p-4"><b>Configuration</b><ul className="mt-2 text-sm"><li>{h.data.users} users</li><li>Research: {h.data.research.configured ? h.data.research.provider : "not configured"}</li></ul></div></section>}
    {!!h.data?.dead_letters.length && <section><h2 className="mb-2 text-lg font-bold">Failed jobs</h2><div className="panel"><table className="table"><tbody>{h.data.dead_letters.map((d) => <tr key={d.id}><td>{d.kind}</td><td className="max-w-[420px] truncate text-xs">{d.error}</td><td className="text-slate">{fmtDate(d.at)}</td>
      <td><button className="btn btn-ghost px-2 py-1 text-xs" onClick={() => requeue.mutate(d.id)}>Retry</button></td></tr>)}</tbody></table></div></section>}
    <section><h2 className="mb-2 text-lg font-bold">Tenants</h2>{t.isLoading ? <Loading /> : <div className="panel overflow-x-auto"><table className="table"><thead><tr><th>Name</th><th>Plan</th><th>Status</th><th>AI cost (30d)</th><th>Created</th><th /></tr></thead>
      <tbody>{t.data?.items.map((o) => <tr key={o.id}><td>{o.name}</td><td><select className="input py-1" value={o.plan} onChange={(e) => patch.mutate({ id: o.id, body: { plan: e.target.value } })} aria-label={`Plan for ${o.name}`}>
        {["trial", "starter", "growth", "scale", "enterprise"].map((p) => <option key={p}>{p}</option>)}</select></td><td><Badge value={o.status === "active" ? "VERIFIED" : "FAILED"} label={o.status} /></td>
        <td>${o.ai_cost_30d.toFixed(2)}</td><td className="text-slate">{fmtDate(o.created_at)}</td>
        <td><button className="btn btn-ghost px-2 py-1 text-xs" onClick={() => patch.mutate({ id: o.id, body: { status: o.status === "active" ? "suspended" : "active" } })}>{o.status === "active" ? "Suspend" : "Reactivate"}</button></td></tr>)}</tbody></table></div>}</section>
    <section><h2 className="mb-2 text-lg font-bold">Security events</h2><div className="panel overflow-x-auto"><table className="table"><tbody>{s.data?.items.map((e) => <tr key={e.id}><td><Badge value={e.severity === "critical" ? "FAILED" : e.severity === "warn" ? "PARTIAL" : "UNKNOWN"} label={e.severity} /></td>
      <td>{e.kind.replaceAll("_", " ")}</td><td className="text-slate">{e.ip ?? ""}</td><td className="text-slate">{fmtDate(e.at)}</td></tr>)}</tbody></table></div></section>
  </div>;
}
