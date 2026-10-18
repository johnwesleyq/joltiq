"use client";
import { useState } from "react";
import { Loading, PageHead, Pager } from "@/components/ui";
import { fmtDate, type Page } from "@/lib/api";
import { useApi } from "@/lib/hooks";
type E = { id: number; at: string; actor_kind: string; actor_id: string | null; action: string; resource_type: string; resource_id: string | null; data: object; hash: string };
export default function Audit() {
  const [page, setPage] = useState(1); const [action, setAction] = useState("");
  const r = useApi<Page<E>>(["audit", page, action], `/audit?page=${page}&page_size=50${action ? `&action=${encodeURIComponent(action)}` : ""}`);
  const v = useApi<{ ok: boolean; checked: number; broken_at?: number }>(["audit-verify"], "/audit/verify");
  return <div><PageHead title="Audit log"><a className="btn btn-ghost" href="/api/v1/audit/export">Export (NDJSON)</a></PageHead>
    {v.data && <p className={v.data.ok ? "mb-4 text-sm text-verify" : "mb-4 text-sm font-bold text-stop"}>{v.data.ok ? `Hash chain intact across ${v.data.checked} entries.` : `Hash chain broken at entry ${v.data.broken_at}. The log may have been altered.`}</p>}
    <input className="input mb-3 max-w-[300px]" placeholder="Filter by action, e.g. approval" value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }} aria-label="Filter actions" />
    {r.isLoading ? <Loading /> : <div className="panel overflow-x-auto"><table className="table"><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Resource</th><th>Details</th></tr></thead>
      <tbody>{r.data?.items.map((e) => <tr key={e.id}><td className="whitespace-nowrap text-slate">{fmtDate(e.at)}</td><td>{e.actor_kind}</td><td className="font-mono text-xs">{e.action}</td>
        <td className="text-xs">{e.resource_type}</td><td className="max-w-[360px] truncate font-mono text-xs" title={JSON.stringify(e.data)}>{JSON.stringify(e.data)}</td></tr>)}</tbody></table></div>}
    {r.data && <Pager page={page} hasMore={r.data.has_more} onPage={setPage} />}</div>;
}
