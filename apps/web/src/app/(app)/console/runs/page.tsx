"use client";
import Link from "next/link";
import { useState } from "react";
import { Badge, Empty, Loading, PageHead, Pager } from "@/components/ui";
import { fmtDate, type Page } from "@/lib/api";
import { useApi } from "@/lib/hooks";
type R = { id: string; ticket_id: string; state: string; paused_for: string | null; retries: number; cost_usd: number; updated_at: string };
export default function Runs() {
  const [page, setPage] = useState(1);
  const r = useApi<Page<R>>(["runs", page], `/agents/runs?page=${page}`, { refetchInterval: 5000 });
  return <div><PageHead title="AI runs" />
    {r.isLoading ? <Loading /> : !r.data?.items.length ? <Empty>No runs yet. Runs start when a customer chats or a technician asks the assistant on a ticket.</Empty> :
    <div className="panel overflow-x-auto"><table className="table"><thead><tr><th>State</th><th>Waiting for</th><th>Retries</th><th>Cost</th><th>Updated</th><th /></tr></thead>
      <tbody>{r.data.items.map((x) => <tr key={x.id}><td><Link className="underline" href={`/console/runs/${x.id}`}><Badge value={x.state} /></Link></td><td>{x.paused_for ?? "—"}</td><td>{x.retries}</td><td>${x.cost_usd.toFixed(4)}</td>
        <td className="text-slate">{fmtDate(x.updated_at)}</td><td className="space-x-3"><Link className="underline" href={`/console/runs/${x.id}`}>Evidence</Link>{x.ticket_id && <Link className="underline" href={`/console/tickets/${x.ticket_id}`}>Ticket</Link>}</td></tr>)}</tbody></table></div>}
    {r.data && <Pager page={page} hasMore={r.data.has_more} onPage={setPage} />}</div>;
}
