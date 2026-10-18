"use client";
import Link from "next/link";
import { ApprovalCard } from "@/components/RunPanel";
import type { Approval } from "@/components/types";
import { Empty, Loading, PageHead } from "@/components/ui";
import { STAFF, type Page } from "@/lib/api";
import { useApi, useMe } from "@/lib/hooks";
export default function Approvals() {
  const me = useMe();
  const staff = STAFF.includes(me.data?.active_organization?.role ?? "");
  const r = useApi<Page<Approval>>(["approvals"], "/approvals?status=PENDING", { refetchInterval: 4000 });
  return <div className="max-w-[760px]"><PageHead title="Approvals" />
    {r.isLoading ? <Loading /> : !r.data?.items.length ? <Empty>Nothing waiting for your approval.</Empty> :
      <div className="space-y-4">{r.data.items.map((a) => <div key={a.id}><ApprovalCard a={a} staff={staff} />
        {a.ticket_id && <Link className="mt-1 inline-block text-sm underline" href={staff ? `/console/tickets/${a.ticket_id}` : `/portal/tickets/${a.ticket_id}`}>View the ticket</Link>}</div>)}</div>}</div>;
}
