"use client";
import Link from "next/link";
import { useState } from "react";
import { fmtDate, type Page } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { Ticket } from "./types";
import { Badge, Empty, ErrorNote, Loading, Pager } from "./ui";

const FILTERS: [string, string][] = [["Open", "status=NEW&status=OPEN&status=AI_INVESTIGATING&status=AWAITING_CUSTOMER&status=AWAITING_APPROVAL&status=IN_PROGRESS&status=REOPENED&status=ESCALATED"],
  ["Escalated", "status=ESCALATED"], ["Needs approval", "status=AWAITING_APPROVAL"], ["Resolved", "status=RESOLVED&status=CLOSED"], ["All", ""]];

export default function TicketList({ base, staff }: { base: string; staff: boolean }) {
  const [filter, setFilter] = useState(0);
  const [q, setQ] = useState("");
  const [mine, setMine] = useState(false);
  const [page, setPage] = useState(1);
  const qs = [FILTERS[filter]![1], q && `q=${encodeURIComponent(q)}`, mine && "assignee=me", `page=${page}`].filter(Boolean).join("&");
  const r = useApi<Page<Ticket>>(["tickets", qs], `/tickets?${qs}`, { refetchInterval: 8000 });
  return <div>
    <div className="mb-4 flex flex-wrap items-center gap-2">
      {FILTERS.map(([l], i) => <button key={l} onClick={() => { setFilter(i); setPage(1); }} className={i === filter ? "btn btn-primary px-3 py-1" : "btn btn-ghost px-3 py-1"}>{l}</button>)}
      {staff && <label className="ml-2 flex items-center gap-1.5 text-sm"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Assigned to me</label>}
      <input className="input ml-auto max-w-[260px]" placeholder="Search or #number" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} aria-label="Search tickets" />
    </div>
    <ErrorNote error={r.error} />
    {r.isLoading ? <Loading /> : !r.data?.items.length ? <Empty>No tickets here.</Empty> :
      <div className="panel overflow-x-auto"><table className="table">
        <thead><tr><th>#</th><th>Subject</th><th>Status</th><th>Priority</th>{staff && <th>Requester</th>}{staff && <th>Assignee</th>}<th>Resolve by</th><th>Updated</th></tr></thead>
        <tbody>{r.data.items.map((t) => <tr key={t.id}>
          <td className="text-slate">{t.number}</td>
          <td><Link className="font-medium hover:underline" href={`${base}/${t.id}`}>{t.subject}</Link>{t.resolution_verified && <span className="ml-2 text-xs text-verify">verified</span>}</td>
          <td><Badge value={t.status} /></td><td>{t.priority}</td>
          {staff && <td>{t.requester?.name ?? "—"}</td>}{staff && <td>{t.assignee?.name ?? <span className="text-slate">—</span>}</td>}
          <td className={t.resolution_due && new Date(t.resolution_due) < new Date() && !["RESOLVED", "CLOSED", "CANCELLED"].includes(t.status) ? "text-stop" : ""}>{fmtDate(t.resolution_due)}</td>
          <td className="text-slate">{fmtDate(t.updated_at)}</td></tr>)}</tbody></table></div>}
    {r.data && <Pager page={page} hasMore={r.data.has_more} onPage={setPage} />}
  </div>;
}
