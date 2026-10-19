"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import GettingStarted from "@/components/GettingStarted";
import TicketList from "@/components/TicketList";
import { ErrorNote, PageHead } from "@/components/ui";
import { ADMINS, api } from "@/lib/api";
import { useAct, useMe } from "@/lib/hooks";

export default function Queue() {
  const router = useRouter();
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [subject, setSubject] = useState("");
  const [desc, setDesc] = useState("");
  const [prio, setPrio] = useState("normal");
  const create = useAct(() => api<{ id: string }>("/tickets", { json: { subject, description: desc, priority: prio } }), [["tickets"]]);
  return <div>
    <GettingStarted admin={ADMINS.includes(me.data?.active_organization?.role ?? "")} />
    <PageHead title="Ticket queue"><button className="btn btn-primary" onClick={() => setOpen(!open)}>New ticket</button></PageHead>
    {open && <form className="panel mb-6 space-y-3 p-4" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined, { onSuccess: (t) => router.push(`/console/tickets/${t.id}`) }); }}>
      <ErrorNote error={create.error} />
      <input className="input" placeholder="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} required minLength={3} aria-label="Subject" />
      <textarea className="input" placeholder="Description" value={desc} onChange={(e) => setDesc(e.target.value)} aria-label="Description" />
      <div className="flex gap-2"><select className="input max-w-[160px]" value={prio} onChange={(e) => setPrio(e.target.value)} aria-label="Priority">
        {["low", "normal", "high", "urgent"].map((p) => <option key={p}>{p}</option>)}</select><button className="btn btn-primary">Create ticket</button></div>
    </form>}
    <TicketList base="/console/tickets" staff />
  </div>;
}
