"use client";
import Link from "next/link";
import { useState } from "react";
import type { Page } from "@/lib/api";
import { useApi } from "@/lib/hooks";

type Prov = { provider: string; enabled: boolean; approved_for_tenant_data: boolean; key_fingerprint: string | null; platform_key_available: boolean; is_test_fixture: boolean };

/** Explains what JoltIQ is and walks an admin through the 4 things that make it useful, with live progress. */
export default function GettingStarted({ admin }: { admin: boolean }) {
  const docs = useApi<Page<unknown>>(["kdocs", "count"], "/knowledge/documents?page_size=1");
  const models = useApi<{ providers: Prov[] }>(["models"], admin ? "/admin/models" : null);
  const devices = useApi<Page<unknown>>(["devices", "count"], "/devices?page_size=1");
  const [hidden, setHidden] = useState(() => { try { return localStorage.getItem("jq_gs_hidden") === "1"; } catch { return false; } });
  const ai = models.data?.providers.some((p) => !p.is_test_fixture && p.enabled && p.approved_for_tenant_data && (p.key_fingerprint || p.platform_key_available));
  const steps = [
    { done: (docs.data?.total ?? 0) > 0, title: "Teach it your fixes", body: "Import past tickets and help articles. JoltIQ answers from what your team already solved.", href: "/admin/knowledge", cta: "Import history" },
    { done: !!ai, title: "Turn on the AI brain", body: "Add an Anthropic (or OpenAI) API key. Until then a basic built-in test model is used.", href: "/admin/models", cta: "Add AI key", adminOnly: true },
    { done: false, title: "Try it like a customer", body: "Chat with JoltIQ the way your customers will. It asks questions, cites past fixes and proposes a fix.", href: "/portal", cta: "Ask JoltIQ" },
    { done: (devices.data?.total ?? 0) > 0, title: "Let it fix computers", body: "Enroll a test PC with a one-time code. JoltIQ can then take over (with permission) and fix things while the user watches.", href: "/admin/devices", cta: "Add a computer" },
  ].filter((s) => admin || !s.adminOnly);
  const left = steps.filter((s) => !s.done).length;
  if (hidden) return <button className="mb-4 text-sm underline" onClick={() => { setHidden(false); try { localStorage.removeItem("jq_gs_hidden"); } catch { /* ignore */ } }}>Show “How JoltIQ works”</button>;
  return <section className="panel mb-6 p-5">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="max-w-[70ch]">
        <h2 className="text-xl font-bold">How JoltIQ works</h2>
        <p className="mt-1 text-slate">JoltIQ is an AI support technician. Customers chat with it like a chatbot. It answers from your past fixes, troubleshoots step by step,
          and with the customer&apos;s permission takes over their browser or computer to fix the problem, then checks the fix worked. Anything it can&apos;t fix safely lands here in your queue.</p>
      </div>
      <button className="text-sm text-slate underline" onClick={() => { setHidden(true); try { localStorage.setItem("jq_gs_hidden", "1"); } catch { /* ignore */ } }}>Hide</button>
    </div>
    <ol className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">{steps.map((s, i) => <li key={s.title} className={s.done ? "rounded-lg border border-verify bg-verify-soft p-4" : "rounded-lg border border-line p-4"}>
      <div className="text-sm text-slate">Step {i + 1}{s.done && " · done ✓"}</div>
      <b>{s.title}</b><p className="mt-1 text-sm text-slate">{s.body}</p>
      <Link className={s.done ? "btn btn-ghost mt-3 px-3 py-1 text-sm" : "btn btn-primary mt-3 px-3 py-1 text-sm"} href={s.href}>{s.cta}</Link>
    </li>)}</ol>
    {left === 0 && <p className="mt-3 text-sm text-verify">All set. Share the Ask JoltIQ page with your customers.</p>}
  </section>;
}
