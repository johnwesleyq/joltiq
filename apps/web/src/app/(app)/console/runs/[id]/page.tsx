"use client";
import Link from "next/link";
import { use } from "react";
import { Badge, ErrorNote, Loading, PageHead } from "@/components/ui";
import { fmtDate } from "@/lib/api";
import { useApi } from "@/lib/hooks";

type Step = { id: string; ordinal: number; action: string; params: Record<string, unknown>; risk: string; status: string };
type Plan = { id: string; revision: number; summary: string; status: string; plan_hash: string; steps: Step[]; rollback_steps?: { action: string; params: Record<string, unknown> }[];
  rollback_availability?: { status?: string; reason?: string; actions?: string[]; channel?: string } };
type RollbackOutcome = { status: string; reason: string; channel?: string; auto?: boolean; verification_id?: string | null; auto_blocked_reason?: string };
type Check = { index: number; type: string; description: string; status: string; critical?: boolean; observed?: unknown; expected?: unknown; reason?: string };
type Ver = { id: string; status: string; kind?: string; evidence_hash?: string | null; details: Check[]; at: string };
type Run = {
  id: string; ticket_id: string | null; state: string; paused_for: string | null; retries: number; max_retries: number; cost_usd: number;
  outcome_summary: string | null; created_at: string; transitions: { from: string; to: string; reason: string; at: string }[];
  plans: Plan[]; approvals: { id: string; status: string; action: string; risk: string; reason: string; expires_at: string; decided_at: string | null }[];
  verifications: Ver[]; browser_rollback?: RollbackOutcome | null; rollback?: RollbackOutcome | null;
  rollback_snapshot?: Record<string, { before: unknown; after: unknown }>; uses_test_fixture_model: boolean;
};
type Evidence = { ok: boolean | null; items: { verification_id: string; ok: boolean; stored_hash: string | null; recomputed_hash: string | null; audit_hash: string | null; chain_ok: boolean }[] };

const show = (v: unknown) => (v === "" ? "(empty)" : typeof v === "string" ? v : JSON.stringify(v));
const preRef = (p: Record<string, unknown>) =>
  Object.entries(p).map(([k, v]) => `${k}=${v && typeof v === "object" && "$pre" in v ? "‹value read back before the change›"
    : v && typeof v === "object" && "$cmd" in v ? "‹PC journal entry of the change›" : show(v)}`).join(" · ");
const OUTCOME_HELP: Record<string, string> = {
  ROLLED_BACK: "The previous state was restored and independently verified.",
  ROLLBACK_UNVERIFIED: "The inverse ran but its read-back could not confirm the previous state.",
  ROLLBACK_FAILED: "The inverse failed; the change may still be in place.",
  ROLLBACK_BLOCKED: "The inverse was not run (approval no longer valid, session ended, or policy/control blocked it).",
  NO_ROLLBACK_AVAILABLE: "No safe inverse exists for this change; a technician must review it.",
  IN_PROGRESS: "Executing the inverse approved with the plan.",
};

export default function RunDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const r = useApi<Run>(["run", id], `/agents/runs/${id}`, { refetchInterval: 5000 });
  const ev = useApi<Evidence>(["run-evidence", id], `/agents/runs/${id}/verification-evidence`, { refetchInterval: 15000 });
  if (r.isLoading) return <Loading />;
  if (!r.data) return <ErrorNote error={r.error} />;
  const d = r.data;
  const evById = new Map((ev.data?.items ?? []).map((i) => [i.verification_id, i]));
  return <div>
    <PageHead title={`Run ${d.id.slice(0, 8)}`} back={{ href: "/console/runs", label: "AI runs" }}
      description={d.outcome_summary ?? "Plan, approvals, independent verification and rollback for this run."}>
      <Badge value={d.state} />{d.paused_for && d.paused_for.toUpperCase() !== d.state.replace(/^AWAITING_/, "") && <Badge value="PENDING" label={`Waiting: ${d.paused_for.replaceAll("_", " ")}`} />}
    </PageHead>
    <p className="text-sm text-slate">Started {fmtDate(d.created_at)} · retries {d.retries}/{d.max_retries} · cost ${d.cost_usd.toFixed(4)}
      {d.uses_test_fixture_model && " · test fixture model (DEMO)"}{d.ticket_id && <> · <Link className="underline" href={`/console/tickets/${d.ticket_id}`}>Open ticket</Link></>}</p>

    {(d.rollback ?? d.browser_rollback) && (() => { const rb = (d.rollback ?? d.browser_rollback)!; return <div className="panel mt-6 p-4" data-testid="rollback-result">
      <h2 className="font-bold">Rollback</h2>
      <p className="mt-1 flex flex-wrap items-center gap-2"><Badge value={rb.status} />{rb.channel && <span className="text-xs text-slate">via {rb.channel}</span>}
        {rb.auto && <span className="text-xs text-slate">· approved with the original plan hash</span>}</p>
      <p className="mt-1 text-sm">{OUTCOME_HELP[rb.status] ?? ""}</p>
      <p className="text-sm text-slate">{rb.reason}</p>
      {rb.auto_blocked_reason && <p className="mt-1 text-sm text-slate">Automatic rollback not used: {rb.auto_blocked_reason}</p>}
      {Object.keys(d.rollback_snapshot ?? {}).length > 0 && <div className="mt-2 text-sm"><p className="font-medium">Pre-change read-back (from the PC&apos;s journal)</p>
        <ul className="mt-1 space-y-1 text-slate">{Object.entries(d.rollback_snapshot!).map(([k, v]) => <li key={k}><code>{k}</code> before {show(v.before)} → after {show(v.after)}</li>)}</ul></div>}
      <p className="mt-2 text-xs text-slate">A rolled-back run always escalates to a technician.</p></div>; })()}

    <h2 className="mt-8 font-bold">Plans</h2>
    <div className="mt-2 space-y-3">{d.plans.map((p) => <div key={p.id} className="panel p-4">
      <div className="flex flex-wrap items-center gap-2"><span className="font-medium">Revision {p.revision}</span><Badge value={p.status.toUpperCase()} label={p.status} />
        <code className="text-xs text-slate" title="Plan hash bound into every approval">{p.plan_hash.slice(0, 16)}…</code>
        {p.rollback_availability?.status && <Badge value={p.rollback_availability.status} label={`Rollback: ${p.rollback_availability.status.replaceAll("_", " ").toLowerCase()}`} />}</div>
      {p.rollback_availability?.status === "NO_ROLLBACK_AVAILABLE" && <p className="mt-1 text-sm text-slate" data-testid="no-rollback-note">{p.rollback_availability.reason}</p>}
      <p className="mt-1 text-sm">{p.summary}</p>
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm">{p.steps.map((s) => <li key={s.id}><code>{s.action}</code> <Badge value={s.risk} /> <span className="text-slate">{s.status}</span></li>)}</ol>
      {(p.rollback_steps?.length ?? 0) > 0 && <div className="mt-3 border-t border-line pt-2"><p className="text-sm font-medium">Server-computed rollback (approved with this plan)</p>
        <ol className="mt-1 list-decimal space-y-1 pl-5 text-sm text-slate">{p.rollback_steps!.map((s, i) => <li key={i}><code>{s.action}</code> {preRef(s.params)}</li>)}</ol></div>}
    </div>)}</div>

    <h2 className="mt-8 font-bold">Approvals</h2>
    {d.approvals.length === 0 ? <p className="text-sm text-slate">None requested.</p> :
      <ul className="mt-2 space-y-2">{d.approvals.map((a) => <li key={a.id} className="panel flex flex-wrap items-center gap-2 p-3 text-sm">
        <Badge value={a.status} /><code>{a.action}</code><Badge value={a.risk} /><span className="text-slate">{a.reason}</span>
        <Link className="ml-auto underline" href={`/approvals/${a.id}`}>Open</Link></li>)}</ul>}

    <h2 className="mt-8 font-bold">Independent verification</h2>
    <p className="text-sm text-slate">VERIFIED requires at least one critical read-back check to pass. Each result&apos;s evidence is hashed into the audit chain and re-checked here.</p>
    <div className="mt-2 space-y-3">{d.verifications.map((v) => { const e = evById.get(v.id); return <div key={v.id} className="panel p-4" data-testid="verification">
      <div className="flex flex-wrap items-center gap-2"><Badge value={v.status} /><span className="text-sm font-medium">{v.kind === "rollback" ? "Rollback check" : "Fix check"}</span>
        <span className="text-xs text-slate">{fmtDate(v.at)}</span>
        {v.evidence_hash && <span className={`ml-auto text-xs ${e ? (e.ok ? "text-verify" : "text-stop") : "text-slate"}`} data-testid="evidence-status">
          evidence {v.evidence_hash.slice(0, 12)}… {e ? (e.ok ? "· matches audit chain" : "· DOES NOT MATCH audit chain") : ""}</span>}</div>
      <ul className="mt-2 space-y-1 text-sm">{v.details.map((c) => <li key={c.index} className="flex flex-wrap gap-2">
        <Badge value={c.status} /><span>{c.description || c.type}</span>{c.critical === false && <span className="text-xs text-slate">(non-critical)</span>}
        {c.expected !== undefined && c.expected !== null && <span className="text-slate">expected {show(c.expected)}, saw {show(c.observed ?? "")}</span>}
        {c.reason && <span className="text-slate">{c.reason}</span>}</li>)}</ul></div>; })}
      {d.verifications.length === 0 && <p className="text-sm text-slate">No verification yet.</p>}</div>

    <h2 className="mt-8 font-bold">Timeline</h2>
    <ol className="mt-2 space-y-1 text-sm">{d.transitions.map((t, i) => <li key={i} className="flex flex-wrap gap-2">
      <span className="w-40 shrink-0 text-slate">{fmtDate(t.at)}</span><Badge value={t.to} /><span className="text-slate">{t.reason}</span></li>)}</ol>
  </div>;
}

