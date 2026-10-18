"use client";
import { useState } from "react";
import { ErrorNote, PageHead, Unavailable } from "@/components/ui";
import { api } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";
type P = { provider: string; enabled: boolean; approved_for_tenant_data: boolean; models: Record<string, string>; input_cost_per_mtok: number; output_cost_per_mtok: number;
  key_fingerprint: string | null; platform_key_available: boolean; last_test_ok: boolean | null; last_test_error: string | null; is_test_fixture: boolean; default_models: Record<string, string> };
type M = { providers: P[]; budget: { monthly_usd_limit: number; per_run_usd_limit: number; research_requests_per_day: number } | null; research: { configured: boolean; provider: string | null; live: boolean } };
const NAMES: Record<string, string> = { openai: "OpenAI", anthropic: "Anthropic", gemini: "Google Gemini", local_fixture: "Local test model" };
function Provider({ p }: { p: P }) {
  const [key, setKey] = useState(""); const [model, setModel] = useState(p.models.default ?? "");
  const [cin, setCin] = useState(p.input_cost_per_mtok); const [cout, setCout] = useState(p.output_cost_per_mtok);
  const save = useAct((patch: Record<string, unknown>) => api(`/admin/models/${p.provider}`, { method: "PATCH", json: patch }), [["models"]]);
  const test = useAct(() => api<{ ok: boolean; detail: string }>(`/admin/models/${p.provider}/test`, { method: "POST" }), [["models"]]);
  if (p.is_test_fixture) return <div className="panel p-4 text-sm"><b>{NAMES[p.provider]}</b><p className="mt-1 text-slate">A deterministic rule engine used when no AI provider is configured, so workflows can be tested end to end. It isn&apos;t AI, it only runs approved procedures, and it&apos;s disabled in production.</p></div>;
  return <div className="panel space-y-3 p-4">
    <div className="flex flex-wrap items-center gap-3"><b className="text-lg">{NAMES[p.provider]}</b>
      <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={p.enabled} onChange={(e) => save.mutate({ enabled: e.target.checked })} /> Enabled</label>
      <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={p.approved_for_tenant_data} onChange={(e) => save.mutate({ approved_for_tenant_data: e.target.checked })} /> Approved to receive our ticket data</label>
      {p.last_test_ok === true && <span className="text-sm text-verify">Connection OK</span>}{p.last_test_ok === false && <span className="text-sm text-stop">{p.last_test_error}</span>}</div>
    <ErrorNote error={save.error || test.error} />
    <div className="grid gap-2 md:grid-cols-[1fr_1fr_110px_110px_auto] md:items-end">
      <label className="field"><span className="text-sm font-semibold">API key</span><input className="input" type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder={p.key_fingerprint ? `saved ••••${p.key_fingerprint.slice(-4)}` : p.platform_key_available ? "using platform key" : "not set"} /></label>
      <label className="field"><span className="text-sm font-semibold">Default model</span><input className="input" value={model} onChange={(e) => setModel(e.target.value)} placeholder={p.default_models.default} /></label>
      <label className="field"><span className="text-sm font-semibold">$ / M input</span><input className="input" type="number" step="0.01" value={cin} onChange={(e) => setCin(Number(e.target.value))} /></label>
      <label className="field"><span className="text-sm font-semibold">$ / M output</span><input className="input" type="number" step="0.01" value={cout} onChange={(e) => setCout(Number(e.target.value))} /></label>
      <div className="flex gap-2"><button className="btn btn-primary" onClick={() => save.mutate({ ...(key ? { api_key: key } : {}), models: { ...p.models, default: model || p.default_models.default }, input_cost_per_mtok: cin, output_cost_per_mtok: cout }, { onSuccess: () => setKey("") })}>Save</button>
        <button className="btn btn-ghost" onClick={() => test.mutate(undefined)}>Test</button></div></div>
    {test.data && <p className={test.data.ok ? "text-sm text-verify" : "text-sm text-stop"}>{test.data.detail}</p>}
  </div>;
}
export default function Models() {
  const m = useApi<M>(["models"], "/admin/models");
  const [b, setB] = useState<{ monthly_usd_limit?: number; per_run_usd_limit?: number }>({});
  const saveB = useAct(() => api("/admin/ai-budget", { method: "PATCH", json: b }), [["models"]]);
  return <div><PageHead title="AI models" />
    <p className="-mt-3 mb-5 max-w-[70ch] text-slate">Ticket data is only sent to providers you&apos;ve enabled and approved. Tasks try providers in order (Anthropic, OpenAI, Gemini) and fall back if one fails.</p>
    <div className="space-y-4">{m.data?.providers.map((p) => <Provider key={p.provider} p={p} />)}</div>
    {m.data?.budget && <div className="panel mt-6 flex flex-wrap items-end gap-3 p-4"><b className="w-full">Spending limits</b>
      <label className="field"><span className="text-sm font-semibold">Per month ($)</span><input className="input" type="number" defaultValue={m.data.budget.monthly_usd_limit} onChange={(e) => setB({ ...b, monthly_usd_limit: Number(e.target.value) })} /></label>
      <label className="field"><span className="text-sm font-semibold">Per ticket run ($)</span><input className="input" type="number" step="0.5" defaultValue={m.data.budget.per_run_usd_limit} onChange={(e) => setB({ ...b, per_run_usd_limit: Number(e.target.value) })} /></label>
      <button className="btn btn-ghost" onClick={() => saveB.mutate(undefined)}>Save limits</button><ErrorNote error={saveB.error} /></div>}
    <div className="mt-6">{m.data && (m.data.research.configured ? <p className="text-sm">Web research: <b>{m.data.research.provider}</b>{!m.data.research.live && " (local test fixture, not the live web)"}</p>
      : <Unavailable what="Web research" why="No search provider key is set on the server (Brave Search or Tavily)." />)}</div>
  </div>;
}
