"use client";
import clsx from "clsx";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ADMINS, api, STAFF } from "@/lib/api";
import { useApi, useLiveEvents, useMe } from "@/lib/hooks";
import Core from "./Core";
import { Loading } from "./ui";

type Nav = { href: string; label: string; roles?: string[]; platform?: boolean };
const NAV: { group: string; items: Nav[] }[] = [
  { group: "Support", items: [
    { href: "/portal", label: "Ask JoltIQ" }, { href: "/portal/tickets", label: "My tickets", roles: ["customer"] },
    { href: "/portal/devices", label: "My devices", roles: ["customer"] }, { href: "/approvals", label: "Approvals" },
  ] },
  { group: "Technician", items: [
    { href: "/console", label: "Queue", roles: STAFF }, { href: "/console/runs", label: "AI runs", roles: STAFF },
    { href: "/console/sessions", label: "Live sessions", roles: STAFF }, { href: "/console/research", label: "Research", roles: STAFF },
  ] },
  { group: "Admin", items: [
    { href: "/admin/users", label: "People", roles: ADMINS }, { href: "/admin/customers", label: "Customers", roles: STAFF },
    { href: "/admin/knowledge", label: "Knowledge", roles: STAFF }, { href: "/admin/devices", label: "Devices", roles: STAFF },
    { href: "/admin/integrations", label: "Integrations", roles: ADMINS }, { href: "/admin/policies", label: "Policies", roles: ADMINS },
    { href: "/admin/models", label: "AI models", roles: ADMINS }, { href: "/admin/analytics", label: "Analytics", roles: [...ADMINS, "technician", "auditor"] },
    { href: "/admin/billing", label: "Billing", roles: ADMINS }, { href: "/admin/security", label: "Organization security", roles: ADMINS },
    { href: "/admin/settings", label: "Settings & branding", roles: ADMINS },
    { href: "/admin/audit", label: "Audit log", roles: [...ADMINS, "auditor"] },
  ] },
  { group: "Platform", items: [{ href: "/platform", label: "Tenants & health", platform: true }] },
];

export default function AppShell({ children }: { children: ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const path = usePathname();
  const qc = useQueryClient();
  const notes = useApi<{ unread_count: number }>(["notifications", "count"], me.data?.active_organization ? "/notifications?page_size=1" : null);
  useLiveEvents();
  useEffect(() => { if (me.error) router.replace(`/login?next=${encodeURIComponent(path)}`); }, [me.error, router, path]);
  if (me.isLoading || !me.data) return <div className="p-10"><Loading /></div>;
  const m = me.data;
  const role = m.active_organization?.role ?? "";
  const brand = m.active_organization?.branding?.product_name || "JoltIQ";
  const visible = (n: Nav) => (n.platform ? m.user.is_platform_owner : !n.roles || n.roles.includes(role) || m.user.is_platform_owner);

  async function switchOrg(id: string) {
    await api("/auth/switch-organization", { json: { organization_id: id } });
    qc.clear();
    router.push("/portal");
  }
  async function logout() { await api("/auth/logout", { method: "POST" }); qc.clear(); router.replace("/login"); }

  return <div className="grid min-h-screen md:grid-cols-[232px_1fr]">
    <aside className="border-r border-line bg-[#0a0f28]/80 text-ink/90 backdrop-blur md:sticky md:top-0 md:h-screen md:overflow-y-auto">
      <div className="flex items-center gap-2 px-5 py-5">
        <Core size={26} />
        <span className="font-display text-lg font-semibold text-white">{brand}</span>
      </div>
      {m.organizations.length > 1 && <div className="px-4 pb-3">
        <label className="sr-only" htmlFor="org">Organization</label>
        <select id="org" className="input py-1.5 text-sm" value={m.active_organization?.id}
                onChange={(e) => switchOrg(e.target.value)}>
          {m.organizations.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select></div>}
      <nav className="pb-6 text-[0.92rem]">
        {NAV.map((g) => { const items = g.items.filter(visible); return items.length ? <div key={g.group} className="mt-3">
          <div className="px-5 pb-1 text-xs text-slate/70">{g.group}</div>
          {items.map((n) => <Link key={n.href} href={n.href} className={clsx("mx-2 block rounded-lg px-3 py-1.5 hover:bg-surface-2",
            (path === n.href || (n.href !== "/portal" && n.href !== "/console" && path.startsWith(n.href))) && "bg-surface-2 text-white shadow-[inset_3px_0_0_var(--color-ion)]")}>{n.label}</Link>)}
        </div> : null; })}
      </nav>
    </aside>
    <div className="min-w-0">
      <header className="flex items-center justify-end gap-4 border-b border-line bg-paper/60 px-6 py-2.5 text-sm backdrop-blur">
        {!m.user.email_verified && <Link href="/account" className="text-stop">Verify your email</Link>}
        <Link href="/notifications" className="hover:underline">Notifications{notes.data?.unread_count ? ` (${notes.data.unread_count})` : ""}</Link>
        <Link href="/account" className="text-slate hover:text-ink">{m.user.name || m.user.email} · {m.active_organization?.name}</Link>
        <button onClick={logout} className="text-slate hover:text-ink">Sign out</button>
      </header>
      <main className="mx-auto max-w-[1180px] px-6 py-8">{children}</main>
    </div>
  </div>;
}
