"use client";
import { useRouter } from "next/navigation";
import { useRef, useState, useSyncExternalStore } from "react";
import { ErrorNote, PageHead } from "@/components/ui";
import { api } from "@/lib/api";

type ChatOut = { ticket_id: string; run_id: string; ticket_number: number };

/** Start a support conversation. Voice input uses the browser's Web Speech API when available. */
export default function Portal() {
  const router = useRouter();
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [listening, setListening] = useState(false);
  const voice = useSyncExternalStore(() => () => {}, () => "webkitSpeechRecognition" in window || "SpeechRecognition" in window, () => false);
  const recRef = useRef<{ stop: () => void } | null>(null);

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

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (msg.trim().length < 3) return;
    setBusy(true); setErr(null);
    try { const out = await api<ChatOut>("/agents/chat", { json: { message: msg } }); router.push(`/portal/tickets/${out.ticket_id}`); }
    catch (x) { setErr(x); setBusy(false); }
  }

  return <div className="max-w-[720px]">
    <PageHead title="What's going wrong?" />
    <p className="-mt-3 mb-5 max-w-[60ch] text-slate">Describe the problem the way you&apos;d explain it on the phone: what you were doing, what happened, any error message. The assistant will ask follow-up questions and won&apos;t change anything without your permission.</p>
    <form onSubmit={submit} className="space-y-3">
      <ErrorNote error={err} />
      <label htmlFor="msg" className="sr-only">Describe the problem</label>
      <textarea id="msg" className="input min-h-[150px]" value={msg} onChange={(e) => setMsg(e.target.value)}
                placeholder="Example: Outlook closes every time I open a PDF attachment. Started Tuesday after an update." />
      <div className="flex flex-wrap gap-2">
        <button className="btn btn-primary" disabled={busy || msg.trim().length < 3}>{busy ? "Starting…" : "Get help"}</button>
        {voice ? <button type="button" className="btn btn-ghost" onClick={toggleVoice} aria-pressed={listening}>{listening ? "Stop dictation" : "Dictate"}</button>
               : <span className="self-center text-sm text-slate">Voice input isn&apos;t supported in this browser.</span>}
      </div>
    </form>
  </div>;
}
