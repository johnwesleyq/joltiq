"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ApprovalCard } from "@/components/RunPanel";
import type { Run, Session } from "@/components/types";
import { ErrorNote } from "@/components/ui";
import { api, STAFF } from "@/lib/api";
import { useApi, useMe } from "@/lib/hooks";

type ChatOut = { ticket_id: string; run_id: string; ticket_number: number; reply: { content: string } | null };
type Bubble = { id: string; who: "ai" | "you"; text: string };

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

function Avatar() {
  return <span aria-hidden className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-ink"><span className="h-3 w-3 rotate-45 bg-jolt" /></span>;
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
  const [msg, setMsg] = useState("");
  const [local, setLocal] = useState<Bubble[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [listening, setListening] = useState(false);
  const voice = useSyncExternalStore(() => () => {}, () => "webkitSpeechRecognition" in window || "SpeechRecognition" in window, () => false);
  const recRef = useRef<{ stop: () => void } | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const runs = useApi<Run[]>(["run", ticketId ?? "none"], ticketId ? `/tickets/${ticketId}/runs` : null, { refetchInterval: 2000 });
  const sess = useApi<{ items: Session[] }>(["sessions", ticketId ?? "none"], ticketId ? `/sessions?ticket_id=${ticketId}` : null, { refetchInterval: 3000 });
  const run = runs.data?.[0];
  const session = sess.data?.items.find((s) => ["pending_consent", "active", "awaiting_user_auth", "paused"].includes(s.state));

  // Server messages are the source of truth; local bubbles cover the gap until the server has them.
  const server: Bubble[] = (run?.messages ?? []).map((m) => ({ id: m.id, who: m.role === "assistant" ? "ai" : "you", text: m.content }));
  const serverYou = new Set(server.filter((b) => b.who === "you").map((b) => b.text.trim()));
  const bubbles = [...server, ...local.filter((b) => !(b.who === "you" && serverYou.has(b.text.trim())))];
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
    setBusy(true); setErr(null); setMsg("");
    setLocal((l) => [...l, { id: `you-${Date.now()}`, who: "you", text: t }]);
    try {
      const out = await api<ChatOut>("/agents/chat", { json: ticketId ? { message: t, ticket_id: ticketId } : { message: t } });
      if (!ticketId) { setTicketId(out.ticket_id); router.replace(`/portal?t=${out.ticket_id}`); }
      setTicketNo(out.ticket_number);
      if (out.reply?.content) setLocal((l) => [...l, { id: `ai-${Date.now()}`, who: "ai", text: out.reply!.content }]);
    } catch (x) { setErr(x); }
    setBusy(false);
  }

  function reset() { setTicketId(null); setTicketNo(null); setLocal([]); router.replace("/portal"); }

  const empty = bubbles.length === 0;
  return <div className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-[820px] flex-col">
    <header className="flex items-center gap-3 border-b border-line pb-4">
      <Avatar />
      <div className="grow"><div className="font-display text-lg font-bold">{brand}</div>
        <div className="flex items-center gap-1.5 text-sm text-slate"><span className="h-2 w-2 rounded-full bg-verify" />AI support technician · online</div></div>
      {!empty && <button className="btn btn-ghost px-3 py-1 text-sm" onClick={reset}>New conversation</button>}
    </header>

    <div className="grow space-y-4 py-6" aria-live="polite">
      {empty && <div className="space-y-6">
        <div className="flex gap-3"><Avatar /><div className="rounded-2xl rounded-tl-sm bg-white px-4 py-3 shadow-sm">
          <p>Hi{first ? ` ${first}` : ""}, I&apos;m {brand}, your AI support technician. Tell me what&apos;s wrong in your own words.</p>
          <p className="mt-2 text-slate">I can answer from your company&apos;s past fixes and, with your permission, take over your browser or computer and fix it while you watch.</p>
        </div></div>
        <div className="grid gap-2 sm:grid-cols-2">{CAN_DO.map(([t, d]) => <div key={t} className="rounded-lg border border-line bg-white px-4 py-3">
          <b>{t}</b><div className="text-sm text-slate">{d}</div></div>)}</div>
        <div><div className="mb-2 text-sm text-slate">Try one:</div>
          <div className="flex flex-wrap gap-2">{SUGGESTIONS.map((s) => <button key={s} className="rounded-full border border-line bg-white px-3 py-1.5 text-sm hover:border-ink" onClick={() => send(s)}>{s}</button>)}</div></div>
      </div>}

      {bubbles.map((b) => b.who === "ai"
        ? <div key={b.id} className="flex gap-3"><Avatar /><div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-tl-sm bg-white px-4 py-3 shadow-sm">{b.text}</div></div>
        : <div key={b.id} className="flex justify-end"><div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-ink px-4 py-3 text-white">{b.text}</div></div>)}

      {(working || busy) && <div className="flex gap-3"><Avatar /><div className="rounded-2xl rounded-tl-sm bg-white px-4 py-3 text-slate shadow-sm">
        <span className="pulse">{WORKING[run?.state ?? "NEW"] ?? "Working on it…"}</span></div></div>}

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

    <form onSubmit={(e) => { e.preventDefault(); send(msg); }} className="sticky bottom-0 border-t border-line bg-paper pb-4 pt-3">
      <ErrorNote error={err} />
      <div className="flex items-end gap-2 rounded-2xl border border-line bg-white p-2 focus-within:border-ink">
        <label htmlFor="msg" className="sr-only">Message {brand}</label>
        <textarea id="msg" rows={1} className="max-h-40 min-h-[44px] grow resize-none bg-transparent px-2 py-2 outline-none" value={msg}
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
