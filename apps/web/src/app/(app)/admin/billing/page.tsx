"use client";
import { useState } from "react";
import { ErrorNote, Loading, PageHead, Unavailable } from "@/components/ui";
import { api, fmtDate } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";
type B = { subscription: { plan: string; interval: string; status: string; trial_ends_at: string | null; current_period_end: string | null; cancel_at_period_end: boolean };
  limits: Record<string, number | string | boolean>; usage_this_month: Record<string, number>; plans: Record<string, { label: string; monthly_usd: number | null }>;
  prices_note: string; stripe: { configured: boolean; mode: string | null } };
const METER_LABEL: Record<string, string> = { ai_usd: "AI spend ($)", ai_tokens: "AI tokens", research_requests: "Research lookups", browser_minutes: "Browser minutes",
  desktop_minutes: "Desktop minutes", sessions: "Sessions", verified_resolutions: "Verified resolutions", storage_bytes: "Storage (MB)" };
const LIMIT_OF: Record<string, string> = { ai_usd: "ai_usd_month", browser_minutes: "browser_minutes", desktop_minutes: "desktop_minutes" };
export default function Billing() {
  const r = useApi<B>(["billing"], "/billing");
  const [interval, setInterval] = useState("month");
  const checkout = useAct((plan: string) => api<{ url: string }>("/billing/checkout", { json: { plan, interval } }));
  const portal = useAct(() => api<{ url: string }>("/billing/portal", { method: "POST" }));
  if (r.isLoading || !r.data) return <Loading />;
  const b = r.data; const s = b.subscription;
  return <div><PageHead title="Billing">{b.stripe.configured && <button className="btn btn-ghost" onClick={() => portal.mutate(undefined, { onSuccess: (x) => location.assign(x.url) })}>Manage payment and invoices</button>}</PageHead>
    <ErrorNote error={checkout.error || portal.error} />
    {!b.stripe.configured && <div className="mb-4"><Unavailable what="Online checkout" why="Stripe isn't configured for this deployment. Plans can still be set by the platform owner." /></div>}
    <div className="panel mb-6 p-4"><p className="text-lg"><b>{b.plans[s.plan]?.label ?? s.plan}</b> · {s.status.replace("_", " ")}{s.status === "trialing" && s.trial_ends_at && ` until ${fmtDate(s.trial_ends_at)}`}</p>
      {s.current_period_end && <p className="text-sm text-slate">{s.cancel_at_period_end ? "Ends" : "Renews"} {fmtDate(s.current_period_end)}</p>}</div>
    <h2 className="mb-2 text-lg font-bold">This month&apos;s usage</h2>
    <div className="panel mb-6 overflow-x-auto"><table className="table"><tbody>{Object.entries(b.usage_this_month).map(([k, v]) => { const lim = LIMIT_OF[k] ? Number(b.limits[LIMIT_OF[k]!]) : null;
      return <tr key={k}><td>{METER_LABEL[k] ?? k}</td><td>{k === "storage_bytes" ? (v / 1048576).toFixed(1) : k === "ai_usd" ? v.toFixed(2) : Math.round(v).toLocaleString()}{lim ? ` of ${lim.toLocaleString()}` : ""}</td></tr>; })}</tbody></table></div>
    <h2 className="mb-2 text-lg font-bold">Change plan</h2>
    <div className="mb-3 flex gap-2">{["month", "year"].map((i) => <button key={i} className={interval === i ? "btn btn-primary px-3 py-1" : "btn btn-ghost px-3 py-1"} onClick={() => setInterval(i)}>{i === "month" ? "Monthly" : "Annual"}</button>)}</div>
    <div className="flex flex-wrap gap-3">{["starter", "growth", "scale"].map((p) => <button key={p} className="btn btn-ghost" disabled={!b.stripe.configured || checkout.isPending}
      onClick={() => checkout.mutate(p, { onSuccess: (x) => location.assign(x.url) })}>{b.plans[p]?.label} · ${b.plans[p]?.monthly_usd}/mo</button>)}</div>
    <p className="mt-3 text-xs text-slate">{b.prices_note}</p></div>;
}
