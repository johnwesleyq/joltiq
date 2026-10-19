"use client";
import { use, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Badge, ErrorNote, Loading, PageHead } from "@/components/ui";
import type { Session } from "@/components/types";
import { api, STAFF } from "@/lib/api";
import { useAct, useApi, useMe } from "@/lib/hooks";
import DrivePanel from "@/components/DrivePanel";

/** Live session: consent, masked live view, exclusive control, protected sign-in, pause/resume, hand back, emergency stop. */
export default function SessionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const me = useMe();
  const staff = STAFF.includes(me.data?.active_organization?.role ?? "");
  const s = useApi<Session>(["session", id], `/sessions/${id}`, { refetchInterval: 2000 });
  const [frame, setFrame] = useState<string | null>(null);
  const [minutes, setMinutes] = useState(60);
  const [maxRisk, setMaxRisk] = useState("SENSITIVE");
  const [text, setText] = useState("");
  const [secret, setSecret] = useState("");
  const imgRef = useRef<HTMLImageElement>(null);
  const inv = [["session"], ["run"], ["ticket"]];
  const consent = useAct((accept: boolean) => api(`/sessions/${id}/consent`, { json: { accept, minutes, max_risk: maxRisk, recording: false } }), inv);
  const control = useAct((action: "take" | "release") => api(`/sessions/${id}/control`, { json: { action } }), inv);
  const input = useAct((body: Record<string, unknown>) => api(`/sessions/${id}/input`, { json: body }), inv);
  const handback = useAct(() => api(`/sessions/${id}/handback`, { method: "POST" }), inv);
  const pause = useAct(() => api(`/sessions/${id}/pause`, { method: "POST" }), inv);
  const resume = useAct(() => api(`/sessions/${id}/resume`, { method: "POST" }), inv);
  const stop = useAct(() => api(`/sessions/${id}/emergency-stop`, { method: "POST" }), inv);
  const end = useAct(() => api(`/sessions/${id}/terminate`, { method: "POST" }), inv);

  const live = s.data?.kind === "browser" && ["active", "awaiting_user_auth", "paused"].includes(s.data.state);
  useEffect(() => {
    if (!live) return;
    let alive = true;
    let prev: string | null = null;
    const tick = async () => {
      try {
        const r = await fetch(`/api/v1/sessions/${id}/frame`, { cache: "no-store" });
        if (r.ok && alive) { const url = URL.createObjectURL(await r.blob()); setFrame(url); if (prev) URL.revokeObjectURL(prev); prev = url; }
      } catch { /* next tick */ }
      if (alive) setTimeout(tick, 1200);
    };
    tick();
    return () => { alive = false; };
  }, [id, live]);

  if (s.isLoading) return <Loading />;
  if (!s.data) return <ErrorNote error={s.error} />;
  const d = s.data;
  const holder = d.control?.holder_kind;
  const err = consent.error || control.error || input.error || handback.error || pause.error || resume.error || stop.error || end.error;

  function clickFrame(e: React.MouseEvent<HTMLImageElement>) {
    const img = imgRef.current!;
    const r = img.getBoundingClientRect();
    input.mutate({ kind: "click", x: ((e.clientX - r.left) / r.width) * 1280, y: ((e.clientY - r.top) / r.height) * 800 });
  }

  return <div>
    <PageHead title={`${d.kind[0]!.toUpperCase()}${d.kind.slice(1)} session`} back={d.ticket_id ? { href: staff ? `/console/tickets/${d.ticket_id}` : `/portal/tickets/${d.ticket_id}`, label: "Ticket" } : undefined}>
      <Badge value={d.state} />
      {["active", "awaiting_user_auth", "paused"].includes(d.state) && <button className="btn btn-stop" onClick={() => stop.mutate(undefined)}>Emergency stop</button>}
    </PageHead>
    <ErrorNote error={err} />

    {d.state === "pending_consent" && <div className="panel max-w-[640px] space-y-4 p-5">
      <h2 className="text-xl font-bold">Allow the assistant to work {d.kind === "browser" ? "in a web app" : "on your computer"}?</h2>
      {d.kind === "browser" ? <p>It will use a separate, isolated browser that can only open: <b>{d.allowed_domains.join(", ")}</b>. It can&apos;t see your own browser, passwords, or other tabs.</p>
        : <p>The companion app on your computer will show a confirmation and a red banner while the session is active. You can press STOP there at any time.</p>}
      <p className="text-sm text-slate">Sensitive steps still need your approval one by one. If a sign-in is needed, you type it yourself; the assistant pauses and can&apos;t see it.</p>
      {d.can_consent ? <>
        <div className="flex flex-wrap gap-4">
          <label className="field"><span className="text-sm font-semibold">For how long</span>
            <select className="input" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>{[15, 30, 60, 120].map((m) => <option key={m} value={m}>{m} minutes</option>)}</select></label>
          <label className="field"><span className="text-sm font-semibold">Allow up to</span>
            <select className="input" value={maxRisk} onChange={(e) => setMaxRisk(e.target.value)}>
              <option value="READ_ONLY">Looking only</option><option value="LOW_RISK">Low-risk actions</option><option value="SENSITIVE">Sensitive changes (with approval)</option></select></label>
        </div>
        <div className="flex gap-2"><button className="btn btn-primary" onClick={() => consent.mutate(true)}>Allow session</button>
          <button className="btn btn-ghost" onClick={() => consent.mutate(false)}>Decline</button></div>
      </> : <p className="rounded bg-paper p-3 text-sm">Only the customer whose {d.kind === "browser" ? "account" : "computer"} this is can allow the session. They&apos;ve been notified.</p>}
    </div>}

    {d.kind === "browser" && live && <div className="grid gap-6 lg:grid-cols-[1fr_300px]">
      <div>
        <div className="mb-2 flex items-center gap-2 text-sm"><span className="truncate text-slate">{d.browser?.url}</span></div>
        <div className={holder === "ai" ? "rounded-md ring-4 ring-jolt" : "rounded-md ring-1 ring-line"}>
          {/* eslint-disable-next-line @next/next/no-img-element -- live blob: frame, not an optimisable asset */}
          {frame ? <img ref={imgRef} src={frame} alt="Live view of the isolated browser" onClick={d.you_hold_control ? clickFrame : undefined}
                        className={d.you_hold_control ? "w-full cursor-crosshair rounded-md" : "w-full rounded-md"} />
                 : <div className="grid aspect-[16/10] place-items-center text-slate">Connecting to the live view…</div>}
        </div>
        <p className="mt-1 text-xs text-slate">Password, code and card fields are blacked out before the image leaves the isolated browser.</p>
      </div>
      <div className="space-y-4">
        <div className="panel p-4 text-sm">
          <b>Control</b>
          <p className="mt-1">{!holder ? "No one is controlling the session." : d.you_hold_control ? "You have control." : `${holder === "ai" ? "The assistant" : holder === "technician" ? "A technician" : "The customer"} has control.`}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {!d.you_hold_control && <button className="btn btn-primary" onClick={() => control.mutate("take")}>Take control</button>}
            {d.you_hold_control && <button className="btn btn-ghost" onClick={() => handback.mutate(undefined)}>Hand back to assistant</button>}
            {d.state === "paused" ? <button className="btn btn-ghost" onClick={() => resume.mutate(undefined)}>Resume</button>
                                  : <button className="btn btn-ghost" onClick={() => pause.mutate(undefined)}>Pause</button>}
          </div>
        </div>
        {d.state === "awaiting_user_auth" && <div className="rounded-md border-2 border-ink p-4 text-sm">
          <b>Sign-in needed</b>
          <p className="mt-1">Take control, click the field in the live view, then type below. Use the protected box for passwords and codes: it goes straight to the isolated browser and is never stored or shown to the assistant.</p>
        </div>}
        {d.you_hold_control && <div className="panel space-y-3 p-4 text-sm">
          <form onSubmit={(e) => { e.preventDefault(); input.mutate({ kind: "type", text }, { onSuccess: () => setText("") }); }} className="space-y-1">
            <label className="font-semibold" htmlFor="t">Type text</label><input id="t" className="input" value={text} onChange={(e) => setText(e.target.value)} autoComplete="off" /></form>
          <form onSubmit={(e) => { e.preventDefault(); input.mutate({ kind: "type", text: secret, protected: true }, { onSuccess: () => setSecret("") }); }} className="space-y-1">
            <label className="font-semibold" htmlFor="p">Protected entry (password or code)</label><input id="p" className="input" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" /></form>
          <div className="flex flex-wrap gap-1.5">{["Tab", "Enter", "Backspace", "Escape"].map((k) => <button key={k} className="btn btn-ghost px-2 py-1 text-xs" onClick={() => input.mutate({ kind: "key", key: k })}>{k}</button>)}
            <button className="btn btn-ghost px-2 py-1 text-xs" onClick={() => input.mutate({ kind: "scroll", dy: 400 })}>Scroll down</button></div>
        </div>}
        {d.browser?.blocked_requests?.length ? <p className="text-xs text-slate">Blocked {d.browser.blocked_requests.length} request(s) outside the allowed domains.</p> : null}
        <button className="btn btn-ghost" onClick={() => end.mutate(undefined)}>End session</button>
      </div>
    </div>}

    {d.kind === "desktop" && d.state !== "pending_consent" && <div className="panel max-w-[640px] p-5 text-sm">
      <p>{d.consent?.device_local_consent ? "The person at the computer accepted the session. Actions run through the companion app, which shows a banner and a STOP button."
        : d.state === "terminated" ? "This session has ended." : "Waiting for the person at the computer to accept the session in the companion app."}</p>
      {d.state !== "terminated" && <button className="btn btn-ghost mt-3" onClick={() => end.mutate(undefined)}>End session</button>}
    </div>}
    {d.kind === "desktop" && d.state !== "pending_consent" && <div className="mt-6"><DrivePanel sessionId={id} ready={d.state === "active" && !!d.consent?.device_local_consent} /></div>}

    {d.state === "terminated" && <p className="mt-4 text-slate">This session has ended{d.emergency_stopped ? " (emergency stop)" : ""}. {d.ticket_id && <Link className="underline" href={staff ? `/console/tickets/${d.ticket_id}` : `/portal/tickets/${d.ticket_id}`}>Back to the ticket</Link>}</p>}
  </div>;
}
