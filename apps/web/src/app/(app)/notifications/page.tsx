"use client";
import Link from "next/link";
import { Empty, Loading, PageHead } from "@/components/ui";
import { api, fmtDate, type Page } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";
type N = { id: string; category: string; title: string; body: string; link: string | null; read: boolean; at: string };
type P = { category: string; in_app: boolean; email: boolean };
export default function Notifications() {
  const r = useApi<Page<N> & { unread_count: number }>(["notifications"], "/notifications?page_size=50");
  const prefs = useApi<P[]>(["notifications", "prefs"], "/notifications/preferences");
  const read = useAct(() => api("/notifications/read", { json: {} }), [["notifications"]]);
  const setPref = useAct((p: P) => api("/notifications/preferences", { method: "PUT", json: p }), [["notifications", "prefs"]]);
  return <div className="grid gap-10 lg:grid-cols-[1fr_320px]"><div>
    <PageHead title="Notifications">{!!r.data?.unread_count && <button className="btn btn-ghost" onClick={() => read.mutate(undefined)}>Mark all as read</button>}</PageHead>
    {r.isLoading ? <Loading /> : !r.data?.items.length ? <Empty>You&apos;re all caught up.</Empty> :
      <ul className="divide-y divide-line rounded-lg border border-line bg-white">{r.data.items.map((n) => <li key={n.id} className={n.read ? "p-3" : "p-3 shadow-[inset_3px_0_0_var(--color-ink)]"}>
        <div className="flex justify-between gap-4"><b>{n.link ? <Link href={n.link} className="hover:underline">{n.title}</Link> : n.title}</b><span className="shrink-0 text-xs text-slate">{fmtDate(n.at)}</span></div>
        {n.body && <p className="text-sm text-slate">{n.body}</p>}</li>)}</ul>}</div>
    <div><h2 className="mb-3 text-lg font-bold">Delivery</h2>
      <table className="table panel"><thead><tr><th>Type</th><th>In app</th><th>Email</th></tr></thead><tbody>
        {prefs.data?.map((p) => <tr key={p.category}><td>{p.category}</td>
          <td><input type="checkbox" aria-label={`${p.category} in app`} checked={p.in_app} onChange={(e) => setPref.mutate({ ...p, in_app: e.target.checked })} /></td>
          <td><input type="checkbox" aria-label={`${p.category} email`} checked={p.email} disabled={p.category === "security"} onChange={(e) => setPref.mutate({ ...p, email: e.target.checked })} /></td></tr>)}
      </tbody></table><p className="mt-2 text-xs text-slate">Security emails can&apos;t be turned off.</p></div></div>;
}
