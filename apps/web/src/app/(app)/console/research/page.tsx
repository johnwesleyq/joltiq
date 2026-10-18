"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ErrorNote, PageHead, Unavailable } from "@/components/ui";
import { api } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";
export default function Research() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const st = useApi<{ configured: boolean; provider: string | null; live: boolean }>(["research-status"], "/research/status");
  const run = useAct(() => api<{ id: string }>("/research", { json: { query: q } }));
  return <div className="max-w-[720px]"><PageHead title="Research" />
    <p className="-mt-3 mb-4 text-slate">Searches vendor documentation and permitted forums. Personal details are removed from the query before it leaves the platform.</p>
    {st.data && !st.data.configured && <Unavailable what="Web search" why="No search provider is configured. Set a Brave or Tavily API key on the server." />}
    {st.data?.configured && !st.data.live && <p className="mb-3 rounded bg-paper p-2 text-sm text-slate">Using the local test search fixture, not the live web.</p>}
    <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); run.mutate(undefined, { onSuccess: (r) => router.push(`/console/research/${r.id}`) }); }}>
      <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. QuickBooks error H202 multi-user" aria-label="Research query" minLength={3} required />
      <button className="btn btn-primary" disabled={run.isPending || !st.data?.configured}>Search</button></form>
    <div className="mt-3"><ErrorNote error={run.error} /></div></div>;
}
