"use client";
import { use } from "react";
import { ApprovalCard } from "@/components/RunPanel";
import type { Approval } from "@/components/types";
import { ErrorNote, Loading, PageHead } from "@/components/ui";
import { STAFF } from "@/lib/api";
import { useApi, useMe } from "@/lib/hooks";
export default function ApprovalPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const me = useMe();
  const r = useApi<Approval>(["approvals", id], `/approvals/${id}`);
  if (r.isLoading) return <Loading />;
  if (!r.data) return <ErrorNote error={r.error} />;
  return <div className="max-w-[720px]"><PageHead title="Approval request" back={{ href: "/approvals", label: "Approvals" }} />
    {!r.data.can_decide && r.data.status === "PENDING" && <p className="mb-3 text-sm text-slate">You can view this request but someone else needs to decide it.</p>}
    <ApprovalCard a={r.data} staff={STAFF.includes(me.data?.active_organization?.role ?? "")} /></div>;
}
