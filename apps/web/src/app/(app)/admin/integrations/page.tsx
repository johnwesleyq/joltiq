"use client";
import { useState } from "react";
import { Badge, Empty, ErrorNote, PageHead } from "@/components/ui";
import { api, fmtDate } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";
type I = { id: string; kind: string; name: string; status: string; config: Record<string, unknown>; credential: { fingerprint: string } | null;
  last_test_result: { ok: boolean; detail: string } | null; last_sync_at: string | null;
  history: { id: string; direction: string; entity: string; status: string; attempts: number; error: string | null; conflict: unknown; at: string }[] };
type Cat = { implemented: string[]; slack_mock_available?: boolean; teams_mock_available?: boolean; extension_points: { kind: string }[] };
const EVENTS = [["approval_requested", "Approval requested"], ["approval_reminder", "Reminder before expiry"], ["approval_outcome", "Approval outcome"]] as const;
const LABEL: Record<string, string> = { freshdesk: "Freshdesk", generic_webhook: "Outgoing webhook", generic_rest: "Incoming REST API", email_intake: "Email to ticket",
  slack: "Slack approval alerts", teams: "Microsoft Teams approval alerts" };

function ConnectorSettings({ integration }: { integration: I }) {
  const [channel, setChannel] = useState(String(integration.config.channel ?? ""));
  const [events, setEvents] = useState<string[]>((integration.config.events as string[]) ?? []);
  const path = integration.kind === "slack" ? "slack" : "teams";
  const save = useAct(() => api(`/integrations/${integration.id}/${path}`, { method: "PATCH", json: { ...(path === "slack" ? { channel } : {}), events } }), [["integrations"]]);
  return <div className="mt-3 space-y-2 border-t border-line pt-3" data-testid={`${path}-settings`}>
    {path === "slack" && <label className="field max-w-xs"><span className="text-xs font-semibold">Slack channel</span><input className="input" value={channel} onChange={(event) => setChannel(event.target.value)} /></label>}
    <div className="flex flex-wrap gap-3 text-sm">{EVENTS.map(([key, label]) => <label key={key} className="flex items-center gap-1">
      <input type="checkbox" aria-label={`${integration.kind === "slack" ? "Slack" : "Teams"} event ${label}`} checked={events.includes(key)}
        onChange={(event) => setEvents(event.target.checked ? [...events, key] : events.filter((value) => value !== key))} />{label}</label>)}</div>
    <button className="btn btn-ghost" onClick={() => save.mutate(undefined)}>Save {integration.kind === "slack" ? "Slack" : "Teams"} settings</button>
    {save.isSuccess && <span className="text-sm text-verify">Settings saved</span>}<ErrorNote error={save.error} />
  </div>;
}
export default function Integrations() {
  const list = useApi<I[]>(["integrations"], "/integrations");
  const cat = useApi<Cat>(["icat"], "/integrations/catalog");
  const [kind, setKind] = useState("freshdesk"); const [name, setName] = useState(""); const [domain, setDomain] = useState(""); const [secret, setSecret] = useState(""); const [url, setUrl] = useState("");
  const [hook, setHook] = useState(""); const [token, setToken] = useState(""); const [channel, setChannel] = useState(""); const [useMock, setUseMock] = useState(false);
  const [events, setEvents] = useState<string[]>(EVENTS.map(([key]) => key));
  const [shown, setShown] = useState<string | null>(null);
  const inv = [["integrations"]];
  const connect = useAct(() => api<I & { generated_secret?: string }>("/integrations", { json: { kind, name: name || LABEL[kind], secret: secret || undefined,
    config: kind === "freshdesk" ? { domain } : kind === "generic_webhook" ? { url }
      : kind === "slack" ? { webhook_url: hook || undefined, bot_token: token || undefined, channel: channel || undefined, events, use_mock: useMock || undefined }
      : kind === "teams" ? { webhook_url: hook || undefined, events, use_mock: useMock || undefined } : {} } }), inv);
  const test = useAct((id: string) => api(`/integrations/${id}/test`, { method: "POST" }), inv);
  const sync = useAct((id: string) => api(`/integrations/${id}/sync`, { method: "POST" }), inv);
  const revoke = useAct((id: string) => api(`/integrations/${id}/revoke`, { method: "POST" }), inv);
  const retry = useAct((v: { i: string; j: string }) => api(`/integrations/${v.i}/sync-jobs/${v.j}/retry`, { method: "POST" }), inv);
  return <div><PageHead title="Integrations" description="Connect ticket sources and optional approval alerts. Credentials are encrypted and never shown again." />
    <form className="panel mb-6 grid gap-3 p-4 md:grid-cols-[180px_1fr_1fr_auto] md:items-end" onSubmit={(e) => { e.preventDefault(); connect.mutate(undefined, { onSuccess: (r) => { setShown(r.generated_secret ?? null); setSecret(""); } }); }}>
      <label className="field"><span className="text-sm font-semibold">Connect</span><select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>{cat.data?.implemented.map((k) => <option key={k} value={k}>{LABEL[k] ?? k}</option>)}</select></label>
      <label className="field"><span className="text-sm font-semibold">Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={LABEL[kind]} /></label>
      {kind === "freshdesk" ? <div className="grid grid-cols-2 gap-2"><label className="field"><span className="text-sm font-semibold">Subdomain</span><input className="input" required placeholder="acme" value={domain} onChange={(e) => setDomain(e.target.value)} /></label>
        <label className="field"><span className="text-sm font-semibold">API key</span><input className="input" type="password" required value={secret} onChange={(e) => setSecret(e.target.value)} /></label></div>
       : kind === "generic_webhook" ? <label className="field"><span className="text-sm font-semibold">URL</span><input className="input" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} /></label>
       : kind === "slack" ? <div className="grid gap-2" data-testid="slack-connect">
          <label className="field"><span className="text-sm font-semibold">Incoming webhook URL</span><input className="input" type="password" value={hook} onChange={(event) => setHook(event.target.value)} /></label>
          <label className="field"><span className="text-sm font-semibold">Bot token (optional)</span><input className="input" type="password" value={token} onChange={(event) => setToken(event.target.value)} /></label>
          <label className="field"><span className="text-sm font-semibold">Channel</span><input className="input" value={channel} onChange={(event) => setChannel(event.target.value)} placeholder="#it-approvals" /></label>
          {cat.data?.slack_mock_available && <label className="flex items-center gap-2 text-sm text-stop"><input type="checkbox" checked={useMock} onChange={(event) => setUseMock(event.target.checked)} />Use local MOCK Slack (development only)</label>}
        </div>
       : kind === "teams" ? <div className="grid gap-2" data-testid="teams-connect">
          <label className="field"><span className="text-sm font-semibold">Teams webhook URL</span><input className="input" type="password" value={hook} onChange={(event) => setHook(event.target.value)} /></label>
          {cat.data?.teams_mock_available && <label className="flex items-center gap-2 text-sm text-stop"><input type="checkbox" aria-label="Use local MOCK Teams (dev only)" checked={useMock} onChange={(event) => setUseMock(event.target.checked)} />Use local MOCK Teams (development only)</label>}
        </div>
       : kind === "email_intake" ? <label className="field"><span className="text-sm font-semibold">Shared secret for your mail relay</span><input className="input" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="leave blank to generate" /></label> : <span className="text-sm text-slate">A secret is generated for you.</span>}
      <button className="btn btn-primary">Connect</button>
      <div className="md:col-span-4"><ErrorNote error={connect.error || test.error || sync.error || revoke.error} />
        {shown && <p className="rounded bg-jolt-soft p-2 text-sm">Copy this secret now; it won&apos;t be shown again: <code className="break-all">{shown}</code></p>}</div></form>
    {!list.data?.length ? <Empty>No integrations yet.</Empty> : <div className="space-y-4">{list.data.map((i) => <div key={i.id} className="panel p-4">
      <div className="flex flex-wrap items-center gap-2"><b>{i.name}</b><span className="text-sm text-slate">{LABEL[i.kind] ?? i.kind}</span><Badge value={i.status} />
        {i.credential && <span className="text-xs text-slate">key ••••{i.credential.fingerprint.slice(-4)}</span>}
        <span className="ml-auto flex gap-2">{i.status !== "revoked" && <><button className="btn btn-ghost px-2 py-1 text-xs" onClick={() => test.mutate(i.id)}>{["slack", "teams"].includes(i.kind) ? "Send test message" : "Test"}</button>
          {i.kind === "freshdesk" && <button className="btn btn-ghost px-2 py-1 text-xs" disabled={i.status !== "connected"} onClick={() => sync.mutate(i.id)}>Sync now</button>}
          <button className="btn btn-ghost px-2 py-1 text-xs" onClick={() => confirm("Revoke this integration?") && revoke.mutate(i.id)}>Revoke</button></>}</span></div>
      {i.last_test_result && <p className={i.last_test_result.ok ? "mt-1 text-sm text-verify" : "mt-1 text-sm text-stop"}>{i.last_test_result.detail}</p>}
      {["slack", "teams"].includes(i.kind) && <><p className="mt-1 text-xs text-slate">{i.config.use_mock ? `MOCK ${i.kind === "slack" ? "Slack" : "Teams"} · development only` : "Production connector"}. Approval decisions still happen inside JoltIQ.</p>
        {i.status !== "revoked" && <ConnectorSettings integration={i} />}</>}
      {i.kind === "generic_rest" && <p className="mt-1 text-xs text-slate">POST /api/v1/integrations/inbound/{i.id}/tickets with header Authorization: Bearer &lt;secret&gt;</p>}
      {i.kind === "email_intake" && <p className="mt-1 text-xs text-slate">Forward raw messages to POST /api/v1/integrations/email/inbound/{i.id}</p>}
      {i.history.length > 0 && <details className="mt-2 text-sm"><summary className="cursor-pointer">Sync history (last sync {fmtDate(i.last_sync_at)})</summary>
        <table className="table mt-1"><tbody>{i.history.map((h) => <tr key={h.id}><td>{h.direction} {h.entity}</td><td><Badge value={h.status === "ok" ? "VERIFIED" : h.status === "conflict" ? "PARTIAL" : "FAILED"} label={h.status} /></td>
          <td className="text-xs text-slate">{h.error ?? (h.conflict ? "changed in the other system" : "")}</td><td className="text-slate">{fmtDate(h.at)}</td>
          <td>{["failed", "dead", "conflict"].includes(h.status) && <button className="btn btn-ghost px-2 py-0.5 text-xs" onClick={() => retry.mutate({ i: i.id, j: h.id })}>Retry</button>}</td></tr>)}</tbody></table></details>}
    </div>)}</div>}
    <h2 className="mb-2 mt-8 text-lg font-bold">Not available yet</h2>
    <p className="text-sm text-slate">{cat.data?.extension_points.map((e) => e.kind.replaceAll("_", " ")).join(", ")}. These connectors are planned and documented, but not built.</p>
  </div>;
}
