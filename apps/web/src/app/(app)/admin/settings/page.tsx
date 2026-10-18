"use client";
import { useEffect, useState } from "react";
import { ErrorNote, Field, Loading, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";
type O = { name: string; settings: Record<string, unknown>; branding: Record<string, string>; custom_domain: string | null };
export default function Settings() {
  const r = useApi<O>(["org"], "/organizations/current");
  const [name, setName] = useState(""); const [set, setSet] = useState<Record<string, unknown>>({}); const [brand, setBrand] = useState<Record<string, string>>({}); const [domain, setDomain] = useState("");
  useEffect(() => { if (r.data) { setName(r.data.name); setSet(r.data.settings); setBrand(r.data.branding); setDomain(r.data.custom_domain ?? ""); } }, [r.data]);
  const saveOrg = useAct(() => api("/organizations/current", { method: "PATCH", json: { name, settings: { external_research_enabled: set.external_research_enabled,
    auto_close_on_verified: set.auto_close_on_verified, require_verification_to_resolve: set.require_verification_to_resolve, approval_ttl_minutes: Number(set.approval_ttl_minutes ?? 60),
    retention_days: set.retention_days } } }), [["org"]]);
  const saveBrand = useAct(() => api<{ custom_domain_dns: string | null }>("/admin/branding", { method: "PATCH", json: { ...Object.fromEntries(Object.entries(brand).filter(([, v]) => v)), ...(domain ? { custom_domain: domain } : {}) } }), [["org"], ["me"]]);
  const retention = useAct(() => api<Record<string, number>>("/admin/retention/run", { method: "POST" }));
  if (!r.data) return <Loading />;
  const ret = (set.retention_days as Record<string, number>) ?? {};
  const chk = (k: string, label: string) => <label className="flex items-center gap-2"><input type="checkbox" checked={Boolean(set[k])} onChange={(e) => setSet({ ...set, [k]: e.target.checked })} /> {label}</label>;
  return <div className="max-w-[720px] space-y-10"><PageHead title="Settings & branding" />
    <section className="space-y-3"><h2 className="text-lg font-bold">Organization</h2><ErrorNote error={saveOrg.error} />
      <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
      {chk("external_research_enabled", "Allow the assistant to search vendor docs and forums (redacted queries)")}
      {chk("require_verification_to_resolve", "Require a verified fix or a technician note to resolve a ticket")}
      {chk("auto_close_on_verified", "Close tickets automatically after a verified resolution")}
      <Field label="Approval requests expire after (minutes)"><input className="input max-w-[140px]" type="number" value={Number(set.approval_ttl_minutes ?? 60)} onChange={(e) => setSet({ ...set, approval_ttl_minutes: e.target.value })} /></Field>
      <h3 className="pt-2 font-bold">Data retention (days)</h3>
      <div className="grid grid-cols-3 gap-3">{[["session_events", "Session activity"], ["screenshots", "Verification evidence"], ["notifications", "Notifications"]].map(([k, l]) =>
        <Field key={k} label={l!}><input className="input" type="number" min={1} value={ret[k!] ?? ""} onChange={(e) => setSet({ ...set, retention_days: { ...ret, [k!]: Number(e.target.value) } })} /></Field>)}</div>
      <p className="text-xs text-slate">Session recording is off by default. Audit logs are kept separately and aren&apos;t deleted by retention.</p>
      <div className="flex gap-2"><button className="btn btn-primary" onClick={() => saveOrg.mutate(undefined)}>Save settings</button>
        <button className="btn btn-ghost" onClick={() => retention.mutate(undefined)}>Apply retention now</button></div>
      {saveOrg.isSuccess && <p className="text-sm text-verify">Saved.</p>}{retention.data && <p className="text-sm text-slate">Removed: {Object.entries(retention.data).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(", ")}</p>}</section>
    <section className="space-y-3"><h2 className="text-lg font-bold">Branding</h2><p className="text-sm text-slate">Customers see these in the portal and in emails.</p><ErrorNote error={saveBrand.error} />
      {[["product_name", "Product name", "JoltIQ"], ["logo_url", "Logo URL (https)", "https://"], ["favicon_url", "Favicon URL (https)", "https://"], ["primary_color", "Primary color", "#15233a"],
        ["support_email", "Support email", "help@yourcompany.com"], ["support_phone", "Support phone", ""], ["email_footer", "Email footer", ""]].map(([k, l, ph]) =>
        <Field key={k} label={l!}><input className="input" value={brand[k!] ?? ""} placeholder={ph} onChange={(e) => setBrand({ ...brand, [k!]: e.target.value })} /></Field>)}
      <Field label="Custom domain" hint="Optional, e.g. help.yourcompany.com"><input className="input" value={domain} onChange={(e) => setDomain(e.target.value.toLowerCase())} /></Field>
      <button className="btn btn-primary" onClick={() => saveBrand.mutate(undefined)}>Save branding</button>
      {saveBrand.data?.custom_domain_dns && <p className="text-sm">Point your DNS: <code>{saveBrand.data.custom_domain_dns}</code></p>}</section>
  </div>;
}
