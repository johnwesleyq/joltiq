"use client";
import { useEffect, useState } from "react";
import { Badge, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";

type Step = { idx: number; kind: string; params: Record<string, unknown>; narration: string; status: string; error: string | null; has_screen: boolean; at: string | null };
type Run = {
  id: string; goal: string; state: string; waiting_for: { kind: "approval" | "question" | "secure_input"; text: string } | null;
  step_count: number; max_steps: number; summary: string | null; success: boolean | null; live_model: boolean; has_screen: boolean;
  steps?: Step[]; you_can_answer?: boolean; cost_usd: number;
};
const ACTIVE = ["pending_approval", "running", "waiting_device", "waiting_customer"];

const LABEL: Record<string, string> = {
  "screen.capture": "Looked at the screen", screenshot: "Looked at the screen", left_click: "Clicked", double_click: "Double-clicked",
  right_click: "Right-clicked", triple_click: "Triple-clicked", type: "Typed", key: "Pressed", scroll: "Scrolled", mouse_move: "Moved the pointer",
  left_click_drag: "Dragged", wait: "Waited", open_url: "Opened", ask_customer: "Asked you", request_approval: "Asked for approval",
  request_secure_input: "Handed you the keyboard", finish: "Finished",
};

function detail(s: Step): string {
  const p = s.params as Record<string, string | number | boolean>;
  if (p.text && ["type", "ask_customer", "request_approval", "request_secure_input"].includes(s.kind)) return `“${String(p.text).slice(0, 120)}”`;
  if (s.kind === "key") return String(p.combo ?? "");
  if (s.kind === "open_url") return String(p.url ?? "");
  return "";
}

/** "Let JoltIQ drive": goal → customer approval → live screen + step log → approvals / questions / your-turn-to-type → stop. */
export default function DrivePanel({ sessionId, ready }: { sessionId: string; ready: boolean }) {
  const runs = useApi<Run[]>(["cu-runs", sessionId], `/sessions/${sessionId}/computer-use`, { refetchInterval: 2500 });
  const current = runs.data?.[0];
  const active = current && ACTIVE.includes(current.state);
  const run = useApi<Run>(["cu", current?.id ?? "none"], current ? `/computer-use/${current.id}` : null, { refetchInterval: active ? 1500 : undefined });
  const [goal, setGoal] = useState("");
  const [answer, setAnswer] = useState("");
  const [shot, setShot] = useState<string | null>(null);
  const inv = [["cu-runs"], ["cu"]];
  const start = useAct((g: string) => api(`/sessions/${sessionId}/computer-use`, { json: { goal: g } }), inv);
  const approve = useAct((ok: boolean) => api(`/computer-use/${current!.id}/approve`, { json: { approved: ok } }), inv);
  const respond = useAct((b: Record<string, unknown>) => api(`/computer-use/${current!.id}/respond`, { json: b }), inv);
  const stop = useAct(() => api(`/computer-use/${current!.id}/stop`, { method: "POST" }), inv);
  const r = run.data;
  const steps = r?.steps ?? [];

  useEffect(() => {
    if (!r?.has_screen) return;
    let alive = true;
    let prev: string | null = null;
    (async () => {
      try {
        const res = await fetch(`/api/v1/computer-use/${r.id}/screen`, { cache: "no-store" });
        if (res.ok && alive) { const u = URL.createObjectURL(await res.blob()); setShot(u); prev = u; }
      } catch { /* next refresh */ }
    })();
    return () => { alive = false; if (prev) setTimeout(() => URL.revokeObjectURL(prev!), 3000); };
  }, [r?.id, r?.has_screen, r?.step_count]);

  const err = start.error || approve.error || respond.error || stop.error;

  if (!r || !active) {
    return <div className="panel space-y-3 p-5">
      <h2 className="text-lg font-bold">Let JoltIQ drive</h2>
      <p className="text-sm text-slate">Describe what to fix. JoltIQ looks at the screen and clicks and types for you. You approve the goal first,
        it asks again before anything hard to undo, and you type any passwords yourself.</p>
      {r && <div className="rounded-md bg-paper p-3 text-sm"><Badge value={r.state} /> <b className="ml-1">Last run:</b> {r.goal}
        {r.summary && <p className="mt-1">{r.summary}</p>}</div>}
      <ErrorNote error={err} />
      <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); if (goal.trim()) start.mutate(goal.trim(), { onSuccess: () => setGoal("") }); }}>
        <label className="field"><span className="text-sm font-semibold">Goal</span>
          <textarea className="input min-h-[80px]" value={goal} onChange={(e) => setGoal(e.target.value)} disabled={!ready}
                    placeholder="e.g. On Namecheap, set the A record for @ on mysite.com to 203.0.113.10" /></label>
        <button className="btn btn-primary" disabled={!ready || start.isPending || goal.trim().length < 5}>Start</button>
        {!ready && <p className="text-xs text-slate">Available once the person at the computer accepts the session.</p>}
      </form>
    </div>;
  }

  const w = r.waiting_for;
  return <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
        <Badge value={r.state} /><span className="font-semibold">{r.goal}</span>
        {!r.live_model && <span className="rounded bg-paper px-2 py-0.5 text-xs">Test model (not AI)</span>}
      </div>
      <div className="rounded-md ring-4 ring-jolt">
        {/* eslint-disable-next-line @next/next/no-img-element -- blob: screenshot */}
        {shot ? <img src={shot} alt="Latest screenshot of the customer's screen" className="w-full rounded-md" />
              : <div className="grid aspect-[16/10] place-items-center text-slate">Waiting for the first screenshot…</div>}
      </div>
      <p className="mt-1 text-xs text-slate">Step {r.step_count} of at most {r.max_steps}. Screenshots are kept with each step for the audit trail.</p>
    </div>
    <div className="space-y-4">
      <ErrorNote error={err} />
      {r.state === "pending_approval" && <div className="rounded-md border-2 border-ink p-4 text-sm">
        <b>Allow JoltIQ to control the computer for this?</b><p className="mt-1">“{r.goal}”</p>
        {r.you_can_answer ? <div className="mt-3 flex gap-2"><button className="btn btn-primary" onClick={() => approve.mutate(true)}>Allow</button>
          <button className="btn btn-ghost" onClick={() => approve.mutate(false)}>Decline</button></div>
          : <p className="mt-2 text-slate">Waiting for the customer to approve.</p>}
      </div>}
      {w && <div className="rounded-md border-2 border-ink p-4 text-sm">
        <b>{w.kind === "approval" ? "Approval needed" : w.kind === "question" ? "JoltIQ has a question" : "Your turn to type"}</b>
        <p className="mt-1">{w.text}</p>
        {!r.you_can_answer ? <p className="mt-2 text-slate">Waiting for the customer.</p>
          : w.kind === "approval" ? <div className="mt-3 flex gap-2"><button className="btn btn-primary" onClick={() => respond.mutate({ approved: true })}>Approve</button>
              <button className="btn btn-ghost" onClick={() => respond.mutate({ approved: false })}>Deny</button></div>
          : w.kind === "question" ? <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); respond.mutate({ answer }, { onSuccess: () => setAnswer("") }); }}>
              <input className="input" value={answer} onChange={(e) => setAnswer(e.target.value)} aria-label="Your answer" /><button className="btn btn-primary">Send</button></form>
          : <><p className="mt-2 text-slate">Type it on your own keyboard, directly in the app on your screen. JoltIQ is paused and doesn&apos;t record what you type.</p>
              <button className="btn btn-primary mt-3" onClick={() => respond.mutate({ done: true })}>I&apos;m done, continue</button></>}
      </div>}
      <button className="btn btn-stop w-full" onClick={() => stop.mutate(undefined)}>Stop JoltIQ</button>
      <ol className="panel max-h-[420px] space-y-2 overflow-auto p-3 text-sm" aria-label="Steps">
        {steps.slice().reverse().map((s) => <li key={s.idx} className="border-b border-line pb-2 last:border-0">
          <div className="flex justify-between gap-2"><span><b>{LABEL[s.kind] ?? s.kind}</b> {detail(s)}</span>
            <span className={s.status === "blocked" || s.status === "failed" ? "text-stop" : "text-slate"}>{s.status}</span></div>
          {s.narration && <p className="text-slate">{s.narration}</p>}
          {s.error && <p className="text-stop">{s.error}</p>}
        </li>)}
      </ol>
    </div>
  </div>;
}
