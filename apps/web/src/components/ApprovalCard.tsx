"use client";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAct } from "@/lib/hooks";
import type { Approval } from "./types";
import { Badge, ErrorNote } from "./ui";
import { ConfirmDialog, ParamsView, RiskBadge, span, Textarea, useNow } from "./ui/index";

const humanAction = (action: string) => action.replaceAll(".", " · ").replaceAll("_", " ");

export function ApprovalCard({ a, staff, compact = false }: { a: Approval; staff: boolean; compact?: boolean }) {
  const [comment, setComment] = useState("");
  const now = useNow();
  const decide = useAct((decision: "approve" | "deny") => api(`/approvals/${a.id}/decision`, { json: { decision, comment } }),
    [["run"], ["ticket"], ["approvals"]]);
  const pending = a.status === "PENDING";
  const left = new Date(a.expires_at).getTime() - now;
  const ticketHref = a.ticket_id ? (staff ? `/console/tickets/${a.ticket_id}` : `/portal/tickets/${a.ticket_id}`) : null;
  return <article className="panel overflow-hidden" aria-label={`Approval: ${a.action}`}>
    <header className="flex flex-wrap items-start gap-3 border-b border-line px-5 py-4">
      <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold capitalize">{humanAction(a.action)}</h3><RiskBadge risk={a.risk} />{!pending && <Badge value={a.status} />}
      </div><p className="mt-0.5 font-mono text-xs text-slate">{a.action}</p></div>
      {pending && <span className={left < 0 ? "text-stop" : "text-slate"}>{left < 0 ? "Expired" : `Expires in ${span(left)}`}</span>}
    </header>
    <div className="space-y-4 px-5 py-4">
      <div><div className="text-xs font-semibold text-slate">Why</div><p className="mt-1">{a.reason}</p></div>
      {a.effects && <div><div className="text-xs font-semibold text-slate">What changes</div><p className="mt-1 text-sm">{a.effects}</p></div>}
      {a.rollback?.status && <div data-testid="approval-rollback"><div className="text-xs font-semibold text-slate">If it doesn&apos;t work</div>
        {a.rollback.status === "AVAILABLE" ? <p className="mt-1 text-sm"><Badge value="APPROVED" label="Rollback available" /> JoltIQ can undo this change, verify the restore, and escalate.</p>
          : <p className="mt-1 text-sm"><Badge value={a.rollback.status} /> {a.rollback.reason || "No automatic undo is available."}</p>}</div>}
      {!compact && <div><div className="mb-1.5 text-xs font-semibold text-slate">Exact parameters</div><ParamsView params={a.params} /></div>}
      <div className="flex flex-wrap gap-4 text-sm text-slate">{ticketHref && <Link className="underline" href={ticketHref}>View ticket</Link>}
        {compact && <Link className="underline" href={`/approvals/${a.id}`}>Full details</Link>}</div>
      {pending && a.can_decide !== false && <div className="space-y-3 border-t border-line pt-4">
        <ErrorNote error={decide.error} />
        {staff && <Textarea rows={2} placeholder="Note for the audit log (optional)" value={comment} onChange={(event) => setComment(event.target.value)} aria-label="Decision note" />}
        <div className="flex gap-2">
          <ConfirmDialog trigger={<button className="btn btn-primary" disabled={decide.isPending}>Approve</button>} title="Approve this step?"
            description={<>JoltIQ will run <code>{a.action}</code> with {a.risk.toLowerCase().replaceAll("_", " ")} risk. If the plan changes, approval is required again.</>}
            confirmLabel="Approve and run" onConfirm={() => decide.mutate("approve")} />
          <ConfirmDialog trigger={<button className="btn btn-ghost" disabled={decide.isPending}>Deny</button>} title="Deny this step?"
            description="JoltIQ will not run this step and will replan or hand it to a technician." confirmLabel="Deny" destructive onConfirm={() => decide.mutate("deny")} />
        </div>
      </div>}
      {pending && a.can_decide === false && <p className="text-sm text-slate">You can view this request, but an eligible approver must decide it.</p>}
    </div>
  </article>;
}
