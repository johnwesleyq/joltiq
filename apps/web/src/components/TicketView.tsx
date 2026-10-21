"use client";
import Link from "next/link";
import { useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { useAct, useApi, useMe } from "@/lib/hooks";
import RunPanel from "./RunPanel";
import type { Run, Session, TicketDetail } from "./types";
import { Badge, ErrorNote, Loading, PageHead } from "./ui";

export default function TicketView({ id, staff }: { id: string; staff: boolean }) {
  const t = useApi<TicketDetail>(["ticket", id], `/tickets/${id}`, { refetchInterval: 4000 });
  const runs = useApi<Run[]>(["run", id], `/tickets/${id}/runs`, { refetchInterval: 2500 });
  const sess = useApi<{ items: Session[] }>(["sessions", id], `/sessions?ticket_id=${id}`, { refetchInterval: 4000 });
  const techs = useApi<{ id: string; name: string }[]>(["techs"], staff ? "/users/technicians" : null);
  const me = useMe();
  const [reply, setReply] = useState("");
  const [internal, setInternal] = useState(false);
  const [note, setNote] = useState("");
  const [selectedAttachmentId, setSelectedAttachmentId] = useState("");
  const send = useAct(() => (runs.data?.[0] && !internal && !staff
    ? api("/agents/chat", { json: { message: reply, ticket_id: id, attachment_id: selectedAttachmentId || undefined } })
    : api(`/tickets/${id}/comments`, { json: { body: reply, internal } })), [["ticket"], ["run"]]);
  const setStatus = useAct((status: string) => api(`/tickets/${id}/status`, { json: { status, resolution_note: status === "RESOLVED" ? note || undefined : undefined } }), [["ticket"]]);
  const assign = useAct((assignee_id: string | null) => api(`/tickets/${id}/assign`, { json: { assignee_id } }), [["ticket"]]);
  const startAi = useAct(() => api("/agents/runs", {
    json: { ticket_id: id, attachment_id: selectedAttachmentId || undefined },
  }), [["run"]]);
  const upload = useAct((f: File) => { const fd = new FormData(); fd.append("file", f); return api(`/tickets/${id}/attachments`, { method: "POST", body: fd }); }, [["ticket"]]);

  if (t.isLoading) return <Loading />;
  if (t.error || !t.data) return <ErrorNote error={t.error} />;
  const d = t.data;
  const run = runs.data?.[0];
  const pendingSession = sess.data?.items.find((s) => s.state === "pending_consent" || s.state === "active" || s.state === "awaiting_user_auth");

  return <div>
    <PageHead title={`#${d.number} ${d.subject}`} back={staff ? { href: "/console", label: "Queue" } : { href: "/portal/tickets", label: "My tickets" }}>
      <Badge value={d.status} />{d.resolution_verified && <Badge value="VERIFIED" label="Verified fix" />}
    </PageHead>
    <div className="grid gap-8 lg:grid-cols-[1fr_380px]">
      <div className="space-y-6">
        {pendingSession && <Link href={`/sessions/${pendingSession.id}`} className="block rounded-md border-2 border-jolt bg-jolt-soft px-4 py-3">
          <b>{pendingSession.state === "pending_consent" ? `Permission needed: ${pendingSession.kind} session` : `Live ${pendingSession.kind} session`}</b>
          <span className="block text-sm">{pendingSession.state === "pending_consent" ? "Review what the assistant wants to access, then allow or decline." : "Open the live view, take control, or stop it."}</span>
        </Link>}
        <div className="panel p-4"><p className="whitespace-pre-wrap">{d.description || <span className="text-slate">No description.</span>}</p>
          {d.attachments.length > 0 && <fieldset className="mt-4 border-t border-line pt-3">
            <legend className="px-1 text-sm font-bold">Ticket files</legend>
            {!staff && <p className="mb-2 text-xs text-slate">Choose a file only when you want the assistant to send it to an approved web app.</p>}
            <ul className="space-y-1.5 text-sm">{d.attachments.map((a) => <li key={a.id} className="flex items-center justify-between gap-3">
              <span><a className="underline" href={`/api/v1/tickets/${id}/attachments/${a.id}`}>{a.filename}</a> <span className="text-slate">({Math.ceil(a.size_bytes / 1024)} KB)</span></span>
              {!staff && <label className="flex shrink-0 cursor-pointer items-center gap-1.5 rounded border border-line px-2 py-1 text-xs">
                <input type="radio" name="assistant-attachment" value={a.id} checked={selectedAttachmentId === a.id} onChange={() => setSelectedAttachmentId(a.id)} />
                Use with assistant
              </label>}
            </li>)}</ul>
            {!staff && selectedAttachmentId && <button type="button" className="mt-2 text-xs underline" onClick={() => setSelectedAttachmentId("")}>Don&apos;t send a file</button>}
          </fieldset>}
        </div>
        <div className="space-y-3">
          {d.comments.map((c) => <div key={c.id} className={c.internal ? "rounded-md border border-dashed border-slate/40 bg-paper p-3" : c.author_kind === "ai" ? "rounded-md border-l-4 border-jolt bg-surface p-3" : "panel p-3"}>
            <div className="mb-1 text-xs text-slate">{c.author_kind === "ai" ? "Assistant" : c.author?.name ?? c.author_kind} · {fmtDate(c.created_at)}{c.internal && " · internal note"}</div>
            <div className="whitespace-pre-wrap">{c.body}</div></div>)}
        </div>
        {run && run.messages.length > 0 && <details className="panel p-3"><summary className="cursor-pointer font-bold">Conversation with the assistant</summary>
          <div className="mt-2 space-y-2">{run.messages.map((m) => <div key={m.id} className={m.role === "assistant" ? "border-l-4 border-jolt pl-3" : "pl-4"}>
            <div className="text-xs text-slate">{m.role === "assistant" ? "Assistant" : "You"}</div><div className="whitespace-pre-wrap">{m.content}</div></div>)}</div></details>}
        <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); if (reply.trim()) send.mutate(undefined, { onSuccess: () => setReply("") }); }}>
          <ErrorNote error={send.error || upload.error} />
          <textarea className="input min-h-[90px]" value={reply} onChange={(e) => setReply(e.target.value)} placeholder={staff ? "Reply to the customer…" : "Reply or answer the assistant's questions…"} aria-label="Reply" />
          <div className="flex flex-wrap items-center gap-3">
            <button className="btn btn-primary" disabled={send.isPending || !reply.trim()}>{internal ? "Add internal note" : "Send reply"}</button>
            {staff && <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={internal} onChange={(e) => setInternal(e.target.checked)} /> Internal note</label>}
            <label className="btn btn-ghost cursor-pointer">Attach file<input type="file" className="sr-only" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} /></label>
          </div>
        </form>
      </div>

      <aside className="space-y-6">
        <div className="panel space-y-2 p-4 text-sm">
          <div className="flex justify-between"><span className="text-slate">Priority</span><span>{d.priority}</span></div>
          <div className="flex justify-between"><span className="text-slate">Requester</span><span>{d.requester?.name ?? "—"}</span></div>
          <div className="flex justify-between"><span className="text-slate">Resolve by</span><span>{fmtDate(d.resolution_due)}</span></div>
          <div className="flex justify-between"><span className="text-slate">Source</span><span>{d.source}{d.external_ref ? ` (${d.external_ref})` : ""}</span></div>
          {staff && <div className="flex items-center justify-between gap-2"><span className="text-slate">Assignee</span>
            <select className="input max-w-[180px] py-1" value={d.assignee_id ?? ""} onChange={(e) => assign.mutate(e.target.value || null)}>
              <option value="">Unassigned</option>{techs.data?.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></div>}
          {d.allowed_transitions.length > 0 && <div className="border-t border-line pt-3">
            <ErrorNote error={setStatus.error} />
            {staff && d.allowed_transitions.includes("RESOLVED") && <input className="input mb-2" placeholder="Resolution note (needed without a verified fix)" value={note} onChange={(e) => setNote(e.target.value)} />}
            <div className="flex flex-wrap gap-1.5">{d.allowed_transitions.map((s) => <button key={s} className="btn btn-ghost px-2 py-1 text-xs" onClick={() => setStatus.mutate(s)}>
              {s === "REOPENED" ? "Reopen" : s === "CANCELLED" ? "Cancel ticket" : s.replaceAll("_", " ").toLowerCase()}</button>)}</div>
          </div>}
        </div>
        {run ? <RunPanel run={run} staff={staff} /> :
          <div className="panel p-4"><p className="text-sm text-slate">The assistant hasn&apos;t worked on this ticket yet.</p>
            <ErrorNote error={startAi.error} />
            <button className="btn btn-primary mt-2" onClick={() => startAi.mutate(undefined)}>Ask the assistant</button></div>}
        {staff && <details className="text-sm"><summary className="cursor-pointer font-bold">Timeline ({d.timeline.length})</summary>
          <ol className="mt-1 space-y-0.5">{d.timeline.map((e) => <li key={e.id}><span className="text-slate">{new Date(e.at).toLocaleTimeString()}</span> {e.kind.replaceAll("_", " ")} <span className="text-slate">· {e.actor_kind}</span></li>)}</ol></details>}
        <p className="text-xs text-slate">Signed in as {me.data?.user.email}</p>
      </aside>
    </div>
  </div>;
}
