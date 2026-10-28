"use client";
import { useEffect, useState } from "react";
import { ErrorNote, Loading, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";

type Provider = {
  id: string; display_name: string; kind: string; issuer: string; client_id: string;
  client_secret_set: boolean; scopes: string; domains: string[]; default_role: string;
  role_mapping: Record<string, string>; jit_provisioning: boolean; enforce_sso: boolean;
  enabled: boolean; tested_at: string | null; callback_url: string;
};
type Form = {
  display_name: string; kind: string; issuer: string; client_id: string; client_secret: string;
  scopes: string; domains: string; default_role: string; jit_provisioning: boolean;
  enforce_sso: boolean; enabled: boolean;
};
const blank: Form = { display_name: "Microsoft Entra", kind: "entra", issuer: "", client_id: "", client_secret: "",
  scopes: "openid email profile", domains: "", default_role: "technician", jit_provisioning: false, enforce_sso: false, enabled: false };

export default function OrganizationSecurity() {
  const current = useApi<Provider | null>(["oidc-security"], "/admin/security/oidc");
  const [form, setForm] = useState<Form>(blank);
  const [proof, setProof] = useState<string | null>(null);
  const payload = () => ({ ...form, client_secret: form.client_secret || undefined,
    domains: form.domains.split(",").map((v) => v.trim()).filter(Boolean), role_mapping: {} });
  const test = useAct(() => api<{ test_proof: string }>("/admin/security/oidc/test", { json: payload() }));
  const save = useAct(() => api<Provider>("/admin/security/oidc", { method: "PUT", json: { ...payload(), test_proof: proof } }), [["oidc-security"]]);
  const disable = useAct(() => api("/admin/security/oidc/disable", { method: "POST" }), [["oidc-security"]]);
  useEffect(() => { if (current.data) setForm({ display_name: current.data.display_name, kind: current.data.kind,
    issuer: current.data.issuer, client_id: current.data.client_id, client_secret: "", scopes: current.data.scopes,
    domains: current.data.domains.join(", "), default_role: current.data.default_role,
    jit_provisioning: current.data.jit_provisioning, enforce_sso: current.data.enforce_sso, enabled: current.data.enabled }); }, [current.data]);
  if (current.isLoading) return <Loading />;
  const set = <K extends keyof Form>(key: K, value: Form[K]) => { setForm((old) => ({ ...old, [key]: value })); setProof(null); };
  return <div className="max-w-[780px] space-y-6"><PageHead title="Organization security" />
    <section className="panel space-y-4 p-5"><div><h2 className="text-lg font-bold">Work sign-in (OIDC)</h2>
      <p className="text-sm text-slate">Connect this organization&apos;s Microsoft Entra tenant. It is off until this exact configuration is tested and enabled.</p></div>
      {current.data?.callback_url && <div className="rounded bg-surface-2 p-3 text-sm"><b>Callback URL</b><br /><code className="break-all">{current.data.callback_url}</code></div>}
      {current.data?.client_secret_set && <p className="text-sm text-verify">A client secret is saved. Leave the field blank to keep it.</p>}
      <div className="grid gap-4 md:grid-cols-2">
        <label className="field"><span>Display name</span><input className="input" value={form.display_name} onChange={(e) => set("display_name", e.target.value)} /></label>
        <label className="field"><span>Issuer URL</span><input className="input" value={form.issuer} onChange={(e) => set("issuer", e.target.value)} placeholder="https://login.microsoftonline.com/…/v2.0" /></label>
        <label className="field"><span>Client ID</span><input className="input" value={form.client_id} onChange={(e) => set("client_id", e.target.value)} /></label>
        <label className="field"><span>Client secret</span><input className="input" type="password" value={form.client_secret} onChange={(e) => set("client_secret", e.target.value)} /></label>
        <label className="field md:col-span-2"><span>Email domains (comma-separated)</span><input className="input" value={form.domains} onChange={(e) => set("domains", e.target.value)} /></label>
        <label className="field"><span>New-user role</span><select className="input" value={form.default_role} onChange={(e) => set("default_role", e.target.value)}><option value="technician">Technician</option><option value="auditor">Auditor</option><option value="customer">Customer (existing customer mapping required)</option></select></label>
        <label className="flex items-center gap-2 self-end pb-2"><input type="checkbox" checked={form.jit_provisioning} onChange={(e) => set("jit_provisioning", e.target.checked)} />Allow safe just-in-time users</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />Enable work sign-in</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={form.enforce_sso} onChange={(e) => set("enforce_sso", e.target.checked)} />Require work sign-in</label>
      </div>
      <p className="rounded border border-jolt/30 bg-jolt-soft p-3 text-sm">Before requiring SSO, confirm two administrators can sign in. Break-glass recovery requires server CLI access and the owner&apos;s authenticator code.</p>
      <ErrorNote error={current.error || test.error || save.error || disable.error} />
      {proof && <p className="text-sm text-verify">Connection test passed. Save without changing the fields.</p>}
      <div className="flex flex-wrap gap-2"><button className="btn btn-ghost" onClick={() => test.mutate(undefined, { onSuccess: (r) => setProof(r.test_proof) })}>Test connection</button>
        <button className="btn btn-primary" disabled={!proof} onClick={() => save.mutate(undefined)}>Save tested configuration</button>
        {current.data?.enabled && <button className="btn btn-ghost" onClick={() => confirm("Disable work sign-in?") && disable.mutate(undefined)}>Disable</button>}</div>
    </section>
  </div>;
}
