"use client";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAct } from "@/lib/hooks";
import type { Approval, Run } from "./types";
import { Badge, ErrorNote } from "./ui";

const PAUSE_TEXT: Record<string, string> = {
  customer: "Waiting for your answer to the questions above.", approval: "Waiting for approval of the plan.",
  consent: "Waiting for permission to start the session.", device_consent: "Waiting for the person at the computer to accept the session.",
  user_auth: "Waiting for a sign-in in the session viewer. The assistant can't see what's typed.", human_control: "A person has control of the session.",
  device_result: "Waiting for the computer to report back.", verification: "Checking the result independently…", diagnostics: "Collecting diagnostics from the computer…",
  diagnostic_consent: "Waiting for you to allow the read-only Outlook diagnosis.",
  diagnostic_device_consent: "Waiting for the person at the PC to accept the signed JoltIQ prompt.",
  procedure_diagnostics: "Checking Outlook, the named add-in, company policy, and matching crash evidence…",
  symptom_check: "The fix is applied and checked. The ticket stays open until the original PDF problem is tested.",
  symptom_observation: "Watching the Windows crash log for 60 seconds after your PDF test…",
};

const OUTCOMES: Record<string, { label: string; detail: string; tone: string }> = {
  UNCONFIRMED: { label: "Diagnosis in progress", detail: "JoltIQ has not confirmed a fix yet.", tone: "border-slate/40 bg-paper" },
  FIX_PARTIALLY_APPLIED: { label: "Part of the fix changed this PC", detail: "A change completed, but the full fix did not.", tone: "border-stop bg-stop-soft" },
  RESTORED_PENDING_RESTART: { label: "Setting restored; Outlook restart pending", detail: "The original setting is back and will take effect the next time Outlook starts.", tone: "border-jolt bg-jolt-soft" },
  FIX_APPLIED_VERIFIED: { label: "Fix applied and checked", detail: "The setting and Outlook restart passed their checks. The original PDF problem still needs confirmation.", tone: "border-jolt bg-jolt-soft" },
  RESOLVED_VERIFIED: { label: "Problem resolved and verified", detail: "The PDF opened and no matching Outlook crash was recorded during the observation window.", tone: "border-verify bg-verify-soft" },
  VERIFICATION_FAILED: { label: "A support person needs to review this", detail: "JoltIQ did not claim the problem was resolved.", tone: "border-stop bg-stop-soft" },
};

function OutcomeCard({ run }: { run: Run }) {
  const outcome = OUTCOMES[run.outcome_stage] ?? OUTCOMES.UNCONFIRMED!;
  return <div className={`border-l-4 px-4 py-3 ${outcome.tone}`} aria-label="Support outcome">
    <div className="font-bold">{outcome.label}</div>
    <p className="mt-0.5 text-sm text-slate">{outcome.detail}</p>
  </div>;
}

export function ApprovalCard({ a, staff }: { a: Approval; staff: boolean }) {
  const [comment, setComment] = useState("");
  const decide = useAct((decision: "approve" | "deny") => api(`/approvals/${a.id}/decision`, { json: { decision, comment } }), [["run"], ["ticket"], ["approvals"]]);
  return <div className="rounded-md border border-ink p-4">
    <div className="flex flex-wrap items-center gap-2"><b className="font-mono text-sm">{a.action}</b><Badge value={a.risk} /><Badge value={a.status} /></div>
    <p className="mt-2">{a.reason}</p>
    <p className="mt-1 text-sm text-slate"><b>What changes:</b> {a.effects}</p>
    <pre className="mt-2 overflow-x-auto rounded bg-paper p-2 text-xs">{JSON.stringify(a.params, null, 2)}</pre>
    {a.status === "PENDING" && <div className="mt-3 space-y-2">
      <ErrorNote error={decide.error} />
      {staff && <input className="input" placeholder="Note (optional)" value={comment} onChange={(e) => setComment(e.target.value)} />}
      <div className="flex gap-2">
        <button className="btn btn-primary" disabled={decide.isPending} onClick={() => decide.mutate("approve")}>Approve this step</button>
        <button className="btn btn-ghost" disabled={decide.isPending} onClick={() => decide.mutate("deny")}>Deny</button>
      </div>
      <p className="text-xs text-slate">Expires {new Date(a.expires_at).toLocaleTimeString()}. If the step changes, you&apos;ll be asked again.</p>
    </div>}
  </div>;
}

export default function RunPanel({ run, staff }: { run: Run; staff: boolean }) {
  const plan = run.plans.at(-1);
  const v = run.verifications.at(-1);
  const escalate = useAct(() => api(`/agents/runs/${run.id}/escalate`, { json: { reason: "Technician took over" } }), [["run"], ["ticket"]]);
  const cancel = useAct(() => api(`/agents/runs/${run.id}/cancel`, { method: "POST" }), [["run"], ["ticket"]]);
  const symptom = useAct((result: "pdf_opened" | "still_crashes" | "cannot_test") =>
    api(`/agents/runs/${run.id}/symptom-check`, { json: { result } }), [["run"], ["ticket"]]);
  const active = !["RESOLVED", "ESCALATED", "FAILED", "CANCELLED", "PARTIALLY_RESOLVED"].includes(run.state);
  return <section className="space-y-5">
    <div className="flex flex-wrap items-center gap-2">
      <h2 className="text-lg font-bold">Assistant</h2><Badge value={run.state} />
      {active && !run.paused_for && <span className="pulse text-sm text-slate">working…</span>}
      {run.uses_test_fixture_model && <span className="rounded bg-paper px-1.5 py-0.5 text-xs text-slate" title="No AI provider is configured; a deterministic local test model produced these steps.">Local test model</span>}
    </div>
    <OutcomeCard run={run} />
    {run.paused_for && <p className="rounded-md bg-jolt-soft px-3 py-2 text-sm">{PAUSE_TEXT[run.paused_for] ?? `Paused: ${run.paused_for}`}</p>}

    {run.outcome_stage === "FIX_APPLIED_VERIFIED" && run.paused_for === "symptom_check" && !run.symptom_response &&
      <div className="rounded-md border-2 border-ink p-4" aria-labelledby={`symptom-${run.id}`}>
        <h3 id={`symptom-${run.id}`} className="font-bold">Please open the same PDF attachment now. What happened?</h3>
        <p className="mt-1 text-sm text-slate">Your answer starts an independent Windows crash-log check. It does not resolve the ticket by itself.</p>
        <ErrorNote error={symptom.error} />
        <div className="mt-3 grid gap-2 sm:grid-cols-3">
          <button className="btn btn-primary" disabled={symptom.isPending} onClick={() => symptom.mutate("pdf_opened")}>The PDF opens now</button>
          <button className="btn btn-ghost" disabled={symptom.isPending} onClick={() => symptom.mutate("still_crashes")}>Outlook still closes</button>
          <button className="btn btn-ghost" disabled={symptom.isPending} onClick={() => symptom.mutate("cannot_test")}>I can&apos;t test this now</button>
        </div>
      </div>}

    {run.recovery && <div className="rounded-md border border-stop bg-stop-soft p-4" aria-label="Recovery status">
      <h3 className="font-bold">Recovery status</h3>
      <p className="mt-1 text-sm">{run.recovery.change_remains
        ? "The change remains in place until rollback is verified."
        : run.recovery.rollback_status === "RESTORED_PENDING_RESTART"
          ? "The original setting is restored. It will take effect the next time Outlook starts."
          : "The original setting was restored and verified."}</p>
      {run.recovery.rollback_status && <p className="mt-1 text-xs text-slate">Rollback: {run.recovery.rollback_status.replaceAll("_", " ").toLowerCase()}</p>}
    </div>}

    {plan && <div>
      <h3 className="font-bold">Plan <span className="font-normal text-slate">· revision {plan.revision}</span></h3>
      <p className="text-slate">{plan.summary}</p>
      <ol className="mt-2 space-y-2">{plan.steps.map((s) => <li key={s.id} className="grid grid-cols-[1.5rem_1fr] gap-2">
        <span className="text-slate">{s.ordinal + 1}.</span>
        <div><div className="flex flex-wrap items-center gap-2"><code className="text-sm">{s.action}</code><Badge value={s.risk} />
          <span className="text-xs text-slate">{s.status}</span></div>
          <div className="text-sm text-slate">{s.rationale}</div>
          {s.postconditions.length > 0 && <div className="text-xs text-slate">Check: {s.postconditions.map((p) => p.description || p.type).join("; ")}</div>}</div>
      </li>)}</ol>
    </div>}

    {run.approvals.filter((a) => a.status === "PENDING").map((a) => <ApprovalCard key={a.id} a={a} staff={staff} />)}

    {v && <div className={v.status === "VERIFIED" ? "rounded-md bg-verify-soft p-3" : v.status === "UNKNOWN" ? "rounded-md bg-paper p-3" : "rounded-md bg-stop-soft p-3"}>
      <div className="flex items-center gap-2"><b>Verification</b><Badge value={v.status} /></div>
      <ul className="mt-1 text-sm">{v.details.map((d, i) => <li key={i}>{d.description || d.type}: <b>{d.status}</b>
        {d.observed !== undefined && <span className="text-slate"> (observed {String(d.observed)})</span>}{d.reason && <span className="text-slate"> — {d.reason}</span>}</li>)}</ul>
    </div>}

    {run.evidence.length > 0 && <div>
      <h3 className="font-bold">Sources</h3>
      <ul className="mt-1 space-y-1.5 text-sm">{run.evidence.map((e) => <li key={e.id}>
        <span className="mr-1 font-mono text-xs text-slate">[{e.id}]</span>
        {e.url ? <a className="underline" href={e.url} target="_blank" rel="noopener noreferrer nofollow">{e.citation}</a> : e.citation}
        {e.kind === "external" && <span className="ml-1 text-xs text-slate">· {e.live === false ? "test fixture" : e.source_kind}</span>}
        {e.procedure && <span className="ml-1 text-xs text-verify">· approved procedure</span>}
        {e.flags?.includes("prompt_injection") && <span className="ml-1 text-xs text-stop">· contained instructions (ignored)</span>}
        {e.flags?.includes("contains_commands_never_executed") && <span className="ml-1 text-xs text-stop">· contained commands (never run)</span>}
      </li>)}</ul>
      {run.research_query_id && staff && <Link className="text-sm underline" href={`/console/research/${run.research_query_id}`}>Research details</Link>}
    </div>}

    {staff && run.hypotheses.length > 0 && <div><h3 className="font-bold">Hypotheses</h3>
      <ul className="text-sm">{run.hypotheses.map((h, i) => <li key={i}>{Math.round(h.confidence * 100)}% · {h.statement} {!h.grounded && <span className="text-stop">(no supporting evidence)</span>}</li>)}</ul></div>}
    {staff && run.observations.length > 0 && <details><summary className="cursor-pointer font-bold">Diagnostics ({run.observations.length})</summary>
      <ul className="mt-1 space-y-1 text-xs">{run.observations.map((o, i) => <li key={i} className="break-words font-mono">{o}</li>)}</ul></details>}
    {staff && <details><summary className="cursor-pointer font-bold">State history</summary>
      <ol className="mt-1 text-sm">{run.transitions.map((t, i) => <li key={i}><span className="text-slate">{new Date(t.at).toLocaleTimeString()}</span> {t.from} → <b>{t.to}</b> <span className="text-slate">{t.reason}</span></li>)}</ol>
      <p className="mt-2 text-xs text-slate">{run.model_calls.length} model calls · ${run.cost_usd.toFixed(4)} · retries {run.retries}/{run.max_retries}</p></details>}
    {run.article_id && staff && <Link className="btn btn-ghost" href={`/admin/knowledge/articles/${run.article_id}`}>Review drafted article</Link>}

    {active && <div className="flex gap-2">
      {staff && <button className="btn btn-ghost" onClick={() => escalate.mutate(undefined)}>Take over (escalate)</button>}
      <button className="btn btn-ghost" onClick={() => cancel.mutate(undefined)}>Stop the assistant</button>
    </div>}
  </section>;
}
