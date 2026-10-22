"use client";
import { useEffect, useState } from "react";
import { Badge, ErrorNote } from "@/components/ui";
import type { ComputerUseMode, ComputerUseRun, ComputerUseStep } from "@/components/types";
import { api } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";

const ACTIVE = ["pending_approval", "waiting_local_consent", "running", "waiting_device", "waiting_customer"];

const LABEL: Record<string, string> = {
  "screen.capture": "Checked the screen", "guide.clear": "Cleared the guide", screenshot: "Checked the screen",
  left_click: "Pointed to a click", double_click: "Pointed to a double-click", right_click: "Pointed to a right-click",
  triple_click: "Pointed to a triple-click", type: "Pointed to a field", key: "Pressed", scroll: "Scrolled",
  mouse_move: "Moved the pointer", left_click_drag: "Dragged", wait: "Waited", open_url: "Opened",
  ask_customer: "Asked you", request_approval: "Asked for approval", request_secure_input: "Handed you the keyboard", finish: "Finished",
};

function detail(step: ComputerUseStep): string {
  const p = step.params as Record<string, string | number | boolean>;
  if (p.text && ["type", "ask_customer", "request_approval", "request_secure_input"].includes(step.kind)) return `“${String(p.text).slice(0, 120)}”`;
  if (step.kind === "key") return String(p.combo ?? "");
  if (step.kind === "open_url") return String(p.url ?? "");
  return "";
}

function ModeChoice({ mode, onChange }: { mode: ComputerUseMode; onChange: (mode: ComputerUseMode) => void }) {
  const choices: { id: ComputerUseMode; title: string; promise: string; detail: string; tone: string }[] = [
    { id: "guide", title: "Guide me", promise: "You make every click", detail: "JoltIQ points to the next step and waits for you to do it.", tone: "border-ion/70 bg-ion/10" },
    { id: "fix", title: "Fix it for me", promise: "JoltIQ controls the computer", detail: "You watch while JoltIQ clicks and types. Sensitive steps still pause for approval.", tone: "border-jolt/70 bg-jolt/10" },
  ];
  return <div>
    <h3 id="computer-use-mode-heading" className="mb-3 font-display text-base font-semibold">How should JoltIQ help?</h3>
    <fieldset aria-labelledby="computer-use-mode-heading">
      <legend className="sr-only">Choose Guide me or Fix it for me</legend>
    <div className="grid gap-3 sm:grid-cols-2">
      {choices.map((choice) => <label key={choice.id} className={`relative cursor-pointer rounded-xl border p-4 transition-colors ${mode === choice.id ? choice.tone : "border-line bg-surface/60 hover:border-slate"}`}>
        <input className="peer sr-only" type="radio" name="computer-use-mode" value={choice.id} checked={mode === choice.id} onChange={() => onChange(choice.id)} />
        <span className="flex items-start justify-between gap-3"><span>
          <span className="block font-display text-sm font-semibold">{choice.title}</span>
          <span className="mt-1 block font-semibold text-ink">{choice.promise}</span>
        </span><span aria-hidden="true" className={`mt-1 size-3 rounded-full ${choice.id === "guide" ? "bg-ion" : "bg-jolt"}`} /></span>
        <span className="mt-2 block text-sm text-slate">{choice.detail}</span>
      </label>)}
    </div>
    </fieldset>
  </div>;
}

function ControlRail({ mode, active, waitingKind, onStop, stopping }: {
  mode: ComputerUseMode; active: boolean; waitingKind?: string; onStop: () => void; stopping: boolean;
}) {
  const customerHolds = mode === "guide" || waitingKind === "secure_input" || waitingKind === "guide_action";
  const holder = customerHolds ? "You have control" : active ? "JoltIQ has control" : "You have control";
  return <div className="sticky top-2 z-10 mb-5 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-line bg-paper/95 px-4 py-3 shadow-xl shadow-black/20 backdrop-blur" aria-label="Computer control status">
    <span className="flex items-center gap-2 text-sm"><span aria-hidden="true" className={`size-2.5 rounded-full ${mode === "guide" ? "bg-ion" : "bg-jolt"}`} />
      <b>{mode === "guide" ? "Guide mode" : "Fix mode"}</b></span>
    <span className="text-sm text-slate">{holder}</span>
    <button className="btn btn-stop ml-auto min-w-32" disabled={!active || stopping} onClick={onStop}>Stop JoltIQ</button>
  </div>;
}

function GuideHighlight({ step }: { step?: ComputerUseStep }) {
  if (!step) return null;
  const p = step.params as Record<string, number>;
  const x = Number(p.x), y = Number(p.y), width = Number(p.width), height = Number(p.height);
  const frameWidth = Number(p.frame_width), frameHeight = Number(p.frame_height);
  if (![x, y, width, height, frameWidth, frameHeight].every(Number.isFinite) || frameWidth <= 0 || frameHeight <= 0) return null;
  return <div aria-hidden="true" className="pointer-events-none absolute rounded-md border-4 border-ion shadow-[0_0_0_9999px_rgb(13_19_48/.3),0_0_24px_rgb(92_225_255/.8)]"
    style={{ left: `${x / frameWidth * 100}%`, top: `${y / frameHeight * 100}%`, width: `${width / frameWidth * 100}%`, height: `${height / frameHeight * 100}%` }} />;
}

function LiveScreen({ run, shot }: { run: ComputerUseRun; shot: string | null }) {
  const guideStep = run.waiting_for?.kind === "guide_action" ? run.steps?.slice().reverse().find((step) => step.status === "pending") : undefined;
  return <div>
    <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
      <Badge value={run.state} /><span className="font-semibold">{run.goal}</span>
      {!run.live_model && <span className="rounded bg-paper px-2 py-0.5 text-xs">Test model (not AI)</span>}
    </div>
    <div className={`relative overflow-hidden rounded-md ring-4 ${run.mode === "guide" ? "ring-ion" : "ring-jolt"}`}>
      {/* eslint-disable-next-line @next/next/no-img-element -- blob: screenshot */}
      {shot ? <img src={shot} alt="Latest screenshot of the customer's screen" className="w-full rounded-md" />
            : <div className="grid aspect-[16/10] place-items-center text-slate">Waiting for the first screenshot…</div>}
      {run.mode === "guide" && shot && <GuideHighlight step={guideStep} />}
    </div>
    <p className="mt-2 text-xs text-slate">Step {run.step_count} of at most {run.max_steps}. Each screenshot and result stays in the audit trail.</p>
  </div>;
}

function CustomerPause({ run, answer, setAnswer, approve, respond }: {
  run: ComputerUseRun; answer: string; setAnswer: (answer: string) => void;
  approve: (ok: boolean) => void; respond: (body: Record<string, unknown>) => void;
}) {
  const waiting = run.waiting_for;
  if (run.state === "pending_approval") return <div className="rounded-md border-2 border-ink p-4 text-sm">
    <b>{run.mode === "guide" ? "Allow JoltIQ to guide this task?" : "Allow JoltIQ to control the computer for this task?"}</b>
    <p className="mt-1">“{run.goal}”</p>
    <p className="mt-2 text-slate">{run.mode === "guide" ? "You will make every click. JoltIQ can only point and explain." : "JoltIQ can click and type while you watch. It pauses for sensitive steps."}</p>
    {run.you_can_answer ? <div className="mt-3 flex gap-2"><button className="btn btn-primary" onClick={() => approve(true)}>Allow</button>
      <button className="btn btn-ghost" onClick={() => approve(false)}>Decline</button></div>
      : <p className="mt-2 text-slate">Waiting for the customer to approve.</p>}
  </div>;
  if (run.state === "waiting_local_consent") return <div className="rounded-md border border-ion bg-ion/10 p-4 text-sm">
    <b>One more check on the PC</b><p className="mt-1 text-slate">The person at the computer must accept this exact {run.mode === "guide" ? "Guide" : "Fix"} request in the JoltIQ companion.</p>
  </div>;
  if (!waiting) return null;
  if (waiting.kind === "guide_action") return <div className="rounded-md border-2 border-ion bg-ion/10 p-4 text-sm">
    <b>Your turn</b><p className="mt-1">{waiting.text}</p>
    {run.you_can_answer ? <div className="mt-3 flex flex-wrap gap-2"><button className="btn btn-primary" onClick={() => respond({ done: true })}>I did it</button>
      <button className="btn btn-ghost" onClick={() => respond({ switch_to_fix: true })}>Switch to Fix mode</button></div>
      : <p className="mt-2 text-slate">Waiting for the customer.</p>}
  </div>;
  return <div className="rounded-md border-2 border-ink p-4 text-sm">
    <b>{waiting.kind === "approval" ? "Approval needed" : waiting.kind === "question" ? "JoltIQ has a question" : "Your turn to type"}</b>
    <p className="mt-1">{waiting.text}</p>
    {!run.you_can_answer ? <p className="mt-2 text-slate">Waiting for the customer.</p>
      : waiting.kind === "approval" ? <div className="mt-3 flex gap-2"><button className="btn btn-primary" onClick={() => respond({ approved: true })}>Approve</button>
          <button className="btn btn-ghost" onClick={() => respond({ approved: false })}>Deny</button></div>
      : waiting.kind === "question" ? <form className="mt-3 flex gap-2" onSubmit={(event) => { event.preventDefault(); respond({ answer }); setAnswer(""); }}>
          <input className="input" value={answer} onChange={(event) => setAnswer(event.target.value)} aria-label="Your answer" /><button className="btn btn-primary">Send</button></form>
      : <><p className="mt-2 text-slate">Type it on your own keyboard, directly in the app on your screen. JoltIQ is paused and doesn&apos;t record what you type.</p>
          <button className="btn btn-primary mt-3" onClick={() => respond({ done: true })}>I&apos;m done, continue</button></>}
  </div>;
}

function StepLog({ steps }: { steps: ComputerUseStep[] }) {
  return <ol className="panel max-h-[420px] space-y-2 overflow-auto p-3 text-sm" aria-label="Steps">
    {steps.slice().reverse().map((step) => <li key={step.idx} className="border-b border-line pb-2 last:border-0">
      <div className="flex justify-between gap-2"><span><b>{LABEL[step.kind] ?? step.kind}</b> {detail(step)}</span>
        <span className={step.status === "blocked" || step.status === "failed" ? "text-stop" : "text-slate"}>{step.status}</span></div>
      {step.narration && <p className="text-slate">{step.narration}</p>}
      {step.error && <p className="text-stop">{step.error}</p>}
    </li>)}
  </ol>;
}

export default function DrivePanel({ sessionId, ready }: { sessionId: string; ready: boolean }) {
  const runs = useApi<ComputerUseRun[]>(["cu-runs", sessionId], `/sessions/${sessionId}/computer-use`, { refetchInterval: 2500 });
  const current = runs.data?.[0];
  const active = !!current && ACTIVE.includes(current.state);
  const run = useApi<ComputerUseRun>(["cu", current?.id ?? "none"], current ? `/computer-use/${current.id}` : null, { refetchInterval: active ? 1500 : undefined });
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState<ComputerUseMode>("guide");
  const [answer, setAnswer] = useState("");
  const [shot, setShot] = useState<string | null>(null);
  const inv = [["cu-runs"], ["cu"]];
  const start = useAct((input: { goal: string; mode: ComputerUseMode }) => api(`/sessions/${sessionId}/computer-use`, { json: input }), inv);
  const approve = useAct((ok: boolean) => api(`/computer-use/${run.data!.id}/approve`, { json: { approved: ok } }), inv);
  const respond = useAct((body: Record<string, unknown>) => api(`/computer-use/${run.data!.id}/respond`, { json: body }), inv);
  const stop = useAct(() => api(`/computer-use/${run.data!.id}/stop`, { method: "POST" }), inv);
  const r = run.data;

  useEffect(() => {
    if (!r?.has_screen) return;
    let alive = true;
    let previous: string | null = null;
    (async () => {
      try {
        const response = await fetch(`/api/v1/computer-use/${r.id}/screen`, { cache: "no-store" });
        if (response.ok && alive) { const url = URL.createObjectURL(await response.blob()); setShot(url); previous = url; }
      } catch { /* next refresh */ }
    })();
    return () => { alive = false; if (previous) setTimeout(() => URL.revokeObjectURL(previous!), 3000); };
  }, [r?.id, r?.has_screen, r?.step_count]);

  const selectedMode = active && r ? r.mode : mode;
  const error = start.error || approve.error || respond.error || stop.error;
  return <section aria-label="JoltIQ computer help">
    <ControlRail mode={selectedMode} active={active} waitingKind={r?.waiting_for?.kind} onStop={() => stop.mutate(undefined)} stopping={stop.isPending} />
    {!r || !active ? <div className="panel space-y-5 p-5 sm:p-6">
      <div className="max-w-2xl"><h2 className="text-xl font-semibold">Choose how we work together</h2>
        <p className="mt-2 text-sm text-slate">Both modes show every step and stop immediately when you press Stop. Passwords and verification codes stay with you.</p></div>
      {r && <div className="rounded-md bg-paper p-3 text-sm"><Badge value={r.state} /> <b className="ml-1">Last run:</b> {r.goal}
        {r.summary && <p className="mt-1">{r.summary}</p>}
        {r.success && <p className="mt-1 text-verify">Verified steps were saved as a reviewable knowledge source.</p>}</div>}
      <ErrorNote error={error} />
      <form className="space-y-5" onSubmit={(event) => { event.preventDefault(); if (goal.trim()) start.mutate({ goal: goal.trim(), mode }, { onSuccess: () => setGoal("") }); }}>
        <ModeChoice mode={mode} onChange={setMode} />
        <label className="field"><span className="text-sm font-semibold">What should we fix?</span>
          <textarea className="input min-h-[92px]" value={goal} onChange={(event) => setGoal(event.target.value)} disabled={!ready}
                    placeholder="For example: Outlook closes when I open a PDF attachment." /></label>
        <button className="btn btn-primary" disabled={!ready || start.isPending || goal.trim().length < 5}>Start {mode === "guide" ? "Guide" : "Fix"} mode</button>
        {!ready && <p className="text-xs text-slate">Available once the person at the computer accepts the support session.</p>}
      </form>
    </div> : <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
      <LiveScreen run={r} shot={shot} />
      <div className="space-y-4"><ErrorNote error={error} />
        <CustomerPause run={r} answer={answer} setAnswer={setAnswer} approve={(ok) => approve.mutate(ok)} respond={(body) => respond.mutate(body)} />
        <StepLog steps={r.steps ?? []} />
      </div>
    </div>}
  </section>;
}
