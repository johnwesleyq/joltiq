"use client";
import Link from "next/link";
import { Badge, Empty, Loading, PageHead } from "@/components/ui";
import type { Session } from "@/components/types";
import { fmtDate, type Page } from "@/lib/api";
import { useApi } from "@/lib/hooks";
export default function Sessions() {
  const r = useApi<Page<Session>>(["sessions", "all"], "/sessions?page_size=50", { refetchInterval: 4000 });
  return <div><PageHead title="Live sessions" />
    {r.isLoading ? <Loading /> : !r.data?.items.length ? <Empty>No sessions yet.</Empty> :
    <div className="panel overflow-x-auto"><table className="table"><thead><tr><th>Kind</th><th>State</th><th>Control</th><th>Scope</th><th>Started</th><th /></tr></thead>
      <tbody>{r.data.items.map((s) => <tr key={s.id}><td>{s.kind}</td><td><Badge value={s.state} /></td><td>{s.control?.holder_kind ?? "—"}</td>
        <td className="text-sm">{s.allowed_domains.join(", ") || (s.device_id ? "device" : "—")}</td><td className="text-slate">{fmtDate(s.created_at)}</td>
        <td><Link className="underline" href={`/sessions/${s.id}`}>{["active", "awaiting_user_auth", "paused"].includes(s.state) ? "Watch / take over" : "Details"}</Link></td></tr>)}</tbody></table></div>}</div>;
}
