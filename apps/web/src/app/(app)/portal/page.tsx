"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ApprovalCard } from "@/components/ApprovalCard";
import type { Run, Session } from "@/components/types";
import Core from "@/components/Core";
import { ErrorNote } from "@/components/ui";
import { api, apiStream, STAFF } from "@/lib/api";
import { useApi, useMe } from "@/lib/hooks";

type Meta = { ticket_id: string; run_id: string; ticket_number: number };
type AiStatus = { live_chat: boolean; notice: string | null; provider: string | null; model: string | null; can_connect: boolean };
type Source = { citation?: string; url?: string };
type Bubble = { id: string; who: "ai" | "you"; text: string; sources?: Source[] };

const SUGGESTIONS = [
  "Outlook keeps crashing when I open attachments",
  "My printer shows offline",
  "I can't connect to the VPN",
  "Our website is down after a DNS change",
  "I'm locked out of my account",
];
const CAN_DO = [
  ["Answers instantly", "from your company's own fixes and help articles"],
  ["Troubleshoots with you", "asks the right questions, step by step"],
  ["Fixes it for you", "takes over your browser or computer, only with your permission"],
  ["Gets a human", "hands off to a technician when it can't fix it safely"],
];
const WORKING: Record<string, string> = {
  NEW: "Reading your message…", UNDERSTANDING: "Understanding the problem…", SEARCHING_INTERNAL: "Searching your company's past fixes…",
  RESEARCHING: "Researching the issue online…", DIAGNOSING: "Working out the cause…", PLANNING: "Preparing a fix…",
  EXECUTING: "Applying the fix…", VERIFYING: "Checking that it worked…", RETRYING: "Trying another way…",
};
const DONE = ["RESOLVED", "ESCALATED", "FAILED", "CANCELLED", "PARTIALLY_RESOLVED"];

function Avatar({ thinking = false }: { thinking?: boolean }) {
  return <Core size={34} thinking={thinking} />;
}

function Chat() {
  const router = useRouter();
  const params = useSearchParams();
  const me = useMe();
  const staff = STAFF.includes(me.data?.active_organization?.role ?? "");
  const brand = me.data?.active_organization?.branding?.product_name || "JoltIQ";
  const first = (me.data?.user.name || "").split(" ")[0];
  const [ticketId, setTicketId] = useState<string | null>(params.get("t"));
  const [ticketNo, setTicketNo] = useState<number | null>(null);
  const aiStatus = useApi<AiStatus>(["ai-status"], "/agents/ai-status");
  const [chatNotice, setChatNotice] = useState<string | null>(null);
  const aiNotice = chatNotice ?? aiStatus.data?.notice ?? null;
  const [keyInput, setKeyInput] = useState("");
  const [keyBusy, setKeyBusy] = useState(false);
  const [typing, setTyping] = useState(false);
  const [msg, setMsg] = useState("");
  const [local, setLocal] = useState<Bubble[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [listening, setListening] = useState(false);
  const voice = useSyncExternalStore(() => () => {}, () => "webkitSpeechRecognition" in window || "SpeechRecognition" in window, () => false);
  const recRef = useRef<{ stop: () => void } | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const sequence = useRef(0);

  const runs = useApi<Run[]>(["run", ticketId ?? "none"], ticketId ? `/tickets/${ticketId}/runs` : null, { refetchInterval: 2000 });
  const sess = useApi<{ items: Session[] }>(["sessions", ticketId ?? "none"], ticketId ? `/sessions?ticket_id=${ticketId}` : null, { refetchInterval: 3000 });
  const run = runs.data?.[0];
  const session = sess.data?.items.find((s) => ["pending_consent", "active", "awaiting_user_auth", "paused"].includes(s.state));

  // Server messages are the source of truth; local bubbles cover the gap until the server has them.
  const server: Bubble[] = (run?.messages ?? []).map((m) => ({
    id: m.id, who: m.role === "assistant" ? "ai" : "you", text: m.content,
    sources: (((m as { data?: { citations?: Source[] } }).data?.citations) ?? []).filter((c) => c.citation),
  }));
  const serverYou = new Set(server.filter((b) => b.who === "you").map((b) => b.text.trim()));
  const serverAi = new Set(server.filter((b) => b.who === "ai").map((b) => b.text.trim()));
  const bubbles = [...server, ...local.filter((b) => !(b.who === "you" ? serverYou : serverAi).has(b.text.trim()))];
  const working = !!run && !DONE.includes(run.state) && !run.paused_for;
  const pendingApprovals = run?.approvals.filter((a) => a.status === "PENDING") ?? [];
  const verified = run?.verifications.at(-1)?.status === "VERIFIED";

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [bubbles.length, working, pendingApprovals.length, session?.id]);

  function toggleVoice() {
    if (listening) { recRef.current?.stop(); return; }
    const W = window as unknown as Record<string, new () => { lang: string; interimResults: boolean; onresult: (e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void; onend: () => void; start: () => void; stop: () => void }>;
    const SR = W.SpeechRecognition || W.webkitSpeechRecognition;
    if (!SR) return;
    const r = new SR();
    r.lang = navigator.language || "en-US";
    r.interimResults = false;
    r.onresult = (e) => setMsg((m) => `${m}${m ? " " : ""}${Array.from(e.results).map((x) => x[0]!.transcript).join(" ")}`);
    r.onend = () => setListening(false);
    recRef.current = r;
    r.start();
    setListening(true);
  }

  async function send(text: string) {
    const t = text.trim();
    if (t.length < 3 || busy) return;
    setBusy(true); setTyping(true); setErr(null); setMsg("");
    sequence.current += 1;
    const streamId = `ai-${sequence.current}`;
    setLocal((l) => [...l, { id: `you-${sequence.current}`, who: "you", text: t }]);
    try {
      await apiStream("/agents/chat/stream", ticketId ? { message: t, ticket_id: ticketId } : { message: t }, (name, data) => {
        if (name === "meta") {
          const meta = data as unknown as Meta;
          if (!ticketId) { setTicketId(meta.ticket_id); router.replace(`/portal?t=${meta.ticket_id}`); }
          setTicketNo(meta.ticket_number);
        } else if (name === "delta") {
          setTyping(false);
          const piece = String(data.text ?? "");
          setLocal((items) => items.some((item) => item.id === streamId)
            ? items.map((item) => item.id === streamId ? { ...item, text: item.text + piece } : item)
            : [...items, { id: streamId, who: "ai", text: piece }]);
        } else if (name === "setup_required") setChatNotice(String(data.ai_notice ?? ""));
        else if (name === "done") setChatNotice(null);
        else if (name === "error") setErr(new Error(String(data.message ?? "The AI model did not answer.")));
      });
    } catch (x) { setErr(x); }
    setTyping(false); setBusy(false);
  }

  async function connectKey() {
    if (keyInput.trim().length < 20) return;
    setKeyBusy(true); setErr(null);
    try {
      await api("/admin/models/openai", { method: "PATCH", json: { api_key: keyInput.trim(), enabled: true, approved_for_tenant_data: true } });
      setKeyInput(""); setChatNotice(null); await aiStatus.refetch?.();
    } catch (error) { setErr(error); }
    setKeyBusy(false);
  }

  function reset() { setTicketId(null); setTicketNo(null); setLocal([]); router.replace("/portal"); }

  const empty = bubbles.length === 0;
  return <div className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-[820px] flex-col">
    <header className="flex items-center gap-3 border-b border-line/60 pb-4">
      <Avatar thinking={working} />
      <div className="grow"><div className="font-display text-base font-semibold">{brand}</div>
        <div className="flex items-center gap-1.5 text-sm text-slate"><span className={`h-2 w-2 rounded-full ${aiNotice ? "bg-amber-400" : "bg-verify"}`} />
          {aiNotice ? "Not connected · Connect OpenAI to chat" : `AI technician${aiStatus.data?.model ? ` · ${aiStatus.data.model}` : ""}`}</div></div>
      {!empty && <button className="btn btn-ghost px-3 py-1 text-sm" onClick={reset}>New conversation</button>}
    </header>

    {aiNotice && <div role="status" className="mt-4 rounded-xl border border-amber-400/60 bg-amber-400/10 px-4 py-3 text-sm">
      <strong>Connect OpenAI to start chatting.</strong>{aiStatus.data?.can_connect ? <div className="mt-2 flex gap-2">
        <input type="password" autoComplete="off" aria-label="OpenAI API key" placeholder="sk-…" value={keyInput}
          onChange={(event) => setKeyInput(event.target.value)} className="grow rounded-lg border border-line bg-surface px-3 py-1.5" />
        <button type="button" className="btn btn-primary" disabled={keyBusy || keyInput.trim().length < 20} onClick={connectKey}>
          {keyBusy ? "Connecting…" : "Connect OpenAI"}</button>
      </div> : <span> Ask your administrator to connect it under Admin → Models.</span>}
    </div>}

    <div className="grow space-y-4 py-6" aria-live="polite">
      {empty && <div className="flex flex-col items-center pt-6 text-center">
        <Core size={120} />
        <h1 className="mt-8 max-w-[18ch] text-[clamp(1.7rem,4vw,2.6rem)]">Hi{first ? ` ${first}` : ""}. What&apos;s broken?</h1>
        <p className="mt-3 max-w-[56ch] text-slate">I&apos;m {brand}, an AI technician. Describe the problem in your own words. I&apos;ll check how your team fixed it before,
          and with your OK I can take over your browser or computer and fix it while you watch.</p>
        <div className="mt-8 flex max-w-[680px] flex-wrap justify-center gap-2">{SUGGESTIONS.map((s) => <button key={s}
          className="rounded-full border border-line bg-surface/70 px-3.5 py-1.5 text-sm transition hover:border-ion hover:text-white" onClick={() => send(s)}>{s}</button>)}</div>
        <dl className="mt-10 grid w-full gap-3 text-left sm:grid-cols-2">{CAN_DO.map(([t, d]) => <div key={t} className="rounded-xl border border-line/70 bg-surface/50 px-4 py-3">
          <dt className="font-semibold text-white">{t}</dt><dd className="text-sm text-slate">{d}</dd></div>)}</dl>
      </div>}

      {bubbles.map((b) => b.who === "ai"
        ? <div key={b.id} className="flex gap-3"><Avatar /><div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-tl-sm bg-surface px-4 py-3 shadow-sm">{b.text}{b.sources && b.sources.length > 0 && <div className="mt-2 border-t border-black/10 pt-2 text-xs text-slate">Sources: {b.sources.map((c, i) => <span key={i}>{i > 0 && " · "}{c.url ? <a className="underline" href={c.url} target="_blank" rel="noopener noreferrer">{c.citation}</a> : c.citation}</span>)}</div>}</div></div>
        : <div key={b.id} className="flex justify-end"><div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-gradient-to-br from-plasma to-[#5b4fd6] px-4 py-3 text-white">{b.text}</div></div>)}

      {(typing || (working && !busy)) && <div className="flex gap-3"><Avatar thinking /><div className="rounded-2xl rounded-tl-sm bg-surface px-4 py-3 text-slate shadow-sm">
        <span className="pulse">{typing ? `${brand} is typing…` : WORKING[run?.state ?? "NEW"] ?? "Working on it…"}</span></div></div>}

      {run?.paused_for === "customer" && <p className="pl-12 text-sm text-slate">Answer in the box below.</p>}

      {pendingApprovals.map((a) => <div key={a.id} className="pl-12"><p className="mb-2 font-semibold">I&apos;d like to do this. Is that OK?</p><ApprovalCard a={a} staff={staff} /></div>)}

      {session && <div className="pl-12"><Link href={`/sessions/${session.id}`} className="block rounded-xl border-2 border-jolt bg-jolt-soft px-4 py-3">
        <b>{session.state === "pending_consent" ? `Can I take control of your ${session.kind === "browser" ? "browser" : "computer"} to fix this?` : `I'm working on your ${session.kind === "browser" ? "browser" : "computer"} now`}</b>
        <span className="block text-sm">{session.state === "pending_consent" ? "Review exactly what I'll access, then allow or decline. You can stop me at any time." : "Watch live, take control back, or press stop."}</span>
      </Link></div>}

      {run && DONE.includes(run.state) && <div className={verified ? "ml-12 rounded-xl bg-verify-soft px-4 py-3" : "ml-12 rounded-xl bg-paper px-4 py-3"}>
        <b>{verified ? "Fixed and verified ✓" : run.state === "ESCALATED" ? "Handed to a technician" : run.state === "CANCELLED" ? "Stopped" : "Finished"}</b>
        {run.outcome_summary && <p className="text-sm">{run.outcome_summary}</p>}
        {run.state === "ESCALATED" && <p className="text-sm text-slate">A person will pick this up. You&apos;ll get an update on your ticket.</p>}
      </div>}
      {run?.uses_test_fixture_model && <p className="pl-12 text-xs text-slate">Running on the built-in test model (no AI key added yet), so replies are basic.</p>}
      <div ref={endRef} />
    </div>

    <form onSubmit={(e) => { e.preventDefault(); send(msg); }} className="sticky bottom-0 bg-gradient-to-t from-paper via-paper/95 to-transparent pb-4 pt-6">
      <ErrorNote error={err} />
      <div className="flex items-end gap-2 rounded-3xl border border-line bg-surface/90 p-2 shadow-[0_10px_40px_-20px_rgb(92_225_255/.5)] focus-within:border-ion">
        <label htmlFor="msg" className="sr-only">Message {brand}</label>
        <textarea id="msg" rows={1} className="max-h-40 min-h-[44px] grow resize-none bg-transparent px-2 py-2 outline-none focus-visible:outline-none" value={msg}
                  onChange={(e) => setMsg(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(msg); } }}
                  placeholder={empty ? "Describe the problem… e.g. Outlook closes when I open a PDF" : "Reply…"} />
        {voice && <button type="button" className="btn btn-ghost px-3" onClick={toggleVoice} aria-pressed={listening} aria-label={listening ? "Stop dictation" : "Dictate"}>{listening ? "■" : "🎤"}</button>}
        <button className="btn btn-primary px-4" disabled={busy || msg.trim().length < 3}>Send</button>
      </div>
      <p className="mt-1.5 text-xs text-slate">{ticketId ? <>Saved as ticket{ticketNo ? ` #${ticketNo}` : ""}. <Link className="underline" href={staff ? `/console/tickets/${ticketId}` : `/portal/tickets/${ticketId}`}>Open ticket</Link> · </> : null}
        {brand} never changes anything without your OK, and never sees your passwords.</p>
    </form>
  </div>;
}

/** The assistant: a chat-first support technician (answers, troubleshoots, and with permission fixes things). */
export default function Portal() {
  return <Suspense><Chat /></Suspense>;
}
