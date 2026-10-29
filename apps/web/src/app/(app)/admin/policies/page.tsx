"use client";
import { useState } from "react";
import { Badge, ErrorNote, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";
type R = { id: string; name: string; priority: number; effect: string; match: Record<string, unknown>; approver_roles: string[]; enabled: boolean };
type A = { id: string; channel: string; risk: string; description: string };

function Condition({ match }: { match: Record<string, unknown> }) {
  const parts = Object.entries(match).map(([key, value]) => {
    const label = key === "risk_at_least" ? "risk is at least" : key === "action" ? "action is" : key === "channel" ? "channel is" : key === "domain" ? "website is" : key.replaceAll("_", " ");
    return <span key={key}>{label} <b>{String(value)}</b></span>;
  });
  return parts.length ? <span className="flex flex-wrap gap-x-2 text-sm">When {parts}</span> : <span className="text-slate">Always</span>;
}
export default function Policies() {
  const rules = useApi<R[]>(["rules"], "/admin/policies");
  const catalog = useApi<A[]>(["catalog"], "/devices/actions/catalog");
  const [form, setForm] = useState({ name: "", priority: 50, effect: "require_approval", matchKey: "action", matchVal: "", roles: "technician,admin,org_owner" });
  const [sim, setSim] = useState({ action: "service.restart", key: "name", value: "Spooler" });
  const create = useAct(() => api("/admin/policies", { json: { name: form.name, priority: form.priority, effect: form.effect, match: { [form.matchKey]: form.matchVal },
    approver_roles: form.effect === "require_approval" ? form.roles.split(",").map((s) => s.trim()).filter(Boolean) : [] } }), [["rules"]]);
  const del = useAct((id: string) => api(`/admin/policies/${id}`, { method: "DELETE" }), [["rules"]]);
  const toggle = useAct((r: R) => api(`/admin/policies/${r.id}`, { method: "PUT", json: { ...r, enabled: !r.enabled } }), [["rules"]]);
  const simulate = useAct(() => api<{ effect: string; risk: string; reasons: string[] }>("/admin/policies/simulate", { json: { action: sim.action, params: sim.key.trim() ? { [sim.key.trim()]: sim.value } : {} } }));
  return <div><PageHead title="Policies" description="Write guardrails as readable sentences. JoltIQ evaluates them before every action." />
    <p className="-mt-3 mb-5 max-w-[70ch] text-slate">Rules are checked in priority order, lowest number first, before every action. The first match decides. Anything above low risk with no matching rule needs approval, and destructive actions are always denied by default.</p>
    <div className="panel mb-6 overflow-x-auto"><table className="table"><thead><tr><th>Priority</th><th>Rule</th><th>Effect</th><th>When</th><th>Approvers</th><th /></tr></thead>
      <tbody>{rules.data?.map((r) => <tr key={r.id} className={r.enabled ? "" : "opacity-50"}><td>{r.priority}</td><td>{r.name}</td><td><Badge value={r.effect === "deny" ? "FAILED" : r.effect === "allow" ? "VERIFIED" : "PENDING"} label={r.effect.replace("_", " ")} /></td>
        <td><Condition match={r.match} /></td><td className="text-sm">{r.approver_roles.join(", ") || "—"}</td>
        <td className="whitespace-nowrap"><button className="btn btn-ghost px-2 py-1 text-xs" onClick={() => toggle.mutate(r)}>{r.enabled ? "Disable" : "Enable"}</button> <button className="btn btn-ghost px-2 py-1 text-xs" onClick={() => del.mutate(r.id)}>Delete</button></td></tr>)}</tbody></table></div>
    <div className="grid gap-6 lg:grid-cols-2">
      <form className="panel space-y-3 p-4" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined); }}>
        <h2 className="font-bold">Add a rule</h2><ErrorNote error={create.error} />
        <input className="input" placeholder="Rule name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} aria-label="Rule name" />
        <div className="grid grid-cols-3 gap-2">
          <input className="input" type="number" value={form.priority} onChange={(e) => setForm({ ...form, priority: Number(e.target.value) })} aria-label="Priority" />
          <select className="input" value={form.effect} onChange={(e) => setForm({ ...form, effect: e.target.value })} aria-label="Effect"><option value="require_approval">Require approval</option><option value="deny">Deny</option><option value="allow">Allow</option></select>
          <select className="input" value={form.matchKey} onChange={(e) => setForm({ ...form, matchKey: e.target.value })} aria-label="Match on"><option value="action">Action is</option><option value="risk_at_least">Risk at least</option><option value="channel">Channel is</option><option value="domain">Domain is</option></select></div>
        {form.matchKey === "action" ? <select className="input" value={form.matchVal} onChange={(e) => setForm({ ...form, matchVal: e.target.value })} aria-label="Action"><option value="">Choose an action</option>{catalog.data?.map((a) => <option key={a.id} value={a.id}>{a.id} ({a.risk})</option>)}</select>
         : form.matchKey === "risk_at_least" ? <select className="input" value={form.matchVal} onChange={(e) => setForm({ ...form, matchVal: e.target.value })} aria-label="Risk"><option value="">Choose</option>{["LOW_RISK", "SENSITIVE", "PRIVILEGED", "DESTRUCTIVE"].map((r) => <option key={r}>{r}</option>)}</select>
         : <input className="input" value={form.matchVal} onChange={(e) => setForm({ ...form, matchVal: e.target.value })} placeholder={form.matchKey === "channel" ? "browser or desktop" : "example.com"} aria-label="Value" />}
        {form.effect === "require_approval" && <input className="input" value={form.roles} onChange={(e) => setForm({ ...form, roles: e.target.value })} aria-label="Approver roles" />}
        <button className="btn btn-primary">Add rule</button></form>
      <form className="panel space-y-3 p-4" onSubmit={(e) => { e.preventDefault(); simulate.mutate(undefined); }}>
        <h2 className="font-bold">Try a rule</h2><p className="text-sm text-slate">See what would happen if the assistant proposed this action in an active session.</p><ErrorNote error={simulate.error} />
        <select className="input" value={sim.action} onChange={(e) => setSim({ ...sim, action: e.target.value })} aria-label="Action to test">{catalog.data?.map((a) => <option key={a.id}>{a.id}</option>)}</select>
        <div className="grid grid-cols-2 gap-2"><input className="input" value={sim.key} onChange={(e) => setSim({ ...sim, key: e.target.value })} aria-label="Parameter name" placeholder="Parameter name" />
          <input className="input" value={sim.value} onChange={(e) => setSim({ ...sim, value: e.target.value })} aria-label="Parameter value" placeholder="Parameter value" /></div>
        <button className="btn btn-ghost">Check</button>
        {simulate.data && <div className="text-sm"><Badge value={simulate.data.effect === "deny" ? "FAILED" : simulate.data.effect === "allow" ? "VERIFIED" : "PENDING"} label={simulate.data.effect.replace("_", " ")} /> <Badge value={simulate.data.risk} />
          <ul className="mt-1 list-disc pl-5 text-slate">{simulate.data.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul></div>}</form>
    </div></div>;
}
