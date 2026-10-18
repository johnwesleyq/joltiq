"use client";
import { use, useState } from "react";
import { Badge, ErrorNote, Loading, PageHead } from "@/components/ui";
import { api, fmtDate } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";
type A = { id: string; title: string; status: string; body: Record<string, unknown>; citations: { id: string; citation: string; url?: string; kind: string }[];
  verified_resolution: boolean; executable: boolean; drafted_by_ai: boolean; reviews: { decision: string; comment: string; at: string }[] };
const SECTIONS = ["symptoms", "environment", "prerequisites", "diagnostics", "remediation", "verification"];
export default function Article({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const r = useApi<A>(["article", id], `/knowledge/articles/${id}`);
  if (r.isLoading) return <Loading />;
  if (!r.data) return <ErrorNote error={r.error} />;
  // Re-key on server state so the editor resets when the article changes (e.g. after a review action).
  return <Editor key={`${r.data.id}:${r.data.status}`} id={id} a={r.data} />;
}

function Editor({ id, a }: { id: string; a: A }) {
  const [title, setTitle] = useState(a.title); const [body, setBody] = useState<Record<string, unknown>>(a.body); const [comment, setComment] = useState(""); const [exec, setExec] = useState(false);
  const save = useAct(() => api(`/knowledge/articles/${id}`, { method: "PATCH", json: { title, body } }), [["article", id]]);
  const review = useAct((decision: string) => api(`/knowledge/articles/${id}/review`, { json: { decision, comment, executable: exec } }), [["article", id], ["karts"]]);
  const editable = !["published", "archived"].includes(a.status);
  const external = Boolean(a.body.derived_from_external_research);
  return <div className="max-w-[860px]"><PageHead title="Review article" back={{ href: "/admin/knowledge", label: "Knowledge" }}><Badge value="PENDING" label={a.status} /></PageHead>
    <p className="mb-4 text-sm text-slate">{a.drafted_by_ai ? "Drafted by the assistant" : "Written by staff"}{a.verified_resolution ? " from a verified resolution." : "."}{external && " Based on external research: it can't be made runnable as-is."}</p>
    <ErrorNote error={save.error || review.error} />
    <input className="input mb-4 text-lg font-bold" value={title} onChange={(e) => setTitle(e.target.value)} disabled={!editable} aria-label="Title" />
    {SECTIONS.map((s) => <label key={s} className="field mb-3"><span className="text-sm font-semibold capitalize">{s} <span className="font-normal text-slate">(one per line)</span></span>
      <textarea className="input min-h-[70px]" disabled={!editable} value={((body[s] as string[]) || []).join("\n")} onChange={(e) => setBody({ ...body, [s]: e.target.value.split("\n").filter(Boolean) })} /></label>)}
    <label className="field mb-3"><span className="text-sm font-semibold">Risk</span><input className="input" disabled={!editable} value={String(body.risk ?? "")} onChange={(e) => setBody({ ...body, risk: e.target.value })} /></label>
    {Array.isArray(body.procedure) && <div className="mb-4"><h2 className="font-bold">Runnable procedure</h2>
      <ol className="mt-1 list-decimal pl-6 text-sm">{(body.procedure as { action: string; params: object }[]).map((p, i) => <li key={i}><code>{p.action}</code> {JSON.stringify(p.params)}</li>)}</ol></div>}
    {a.citations.length > 0 && <div className="mb-4"><h2 className="font-bold">Citations</h2><ul className="text-sm">{a.citations.map((c) => <li key={c.id}>{c.url ? <a className="underline" href={c.url} target="_blank" rel="noopener noreferrer">{c.citation}</a> : c.citation}</li>)}</ul></div>}
    <div className="panel space-y-3 p-4">
      {editable && <button className="btn btn-ghost" onClick={() => save.mutate(undefined)}>Save changes</button>}
      <input className="input" placeholder="Review comment" value={comment} onChange={(e) => setComment(e.target.value)} aria-label="Review comment" />
      {a.status === "approved" && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={exec} onChange={(e) => setExec(e.target.checked)} disabled={external} /> Let the assistant run this procedure (requires admin)</label>}
      <div className="flex flex-wrap gap-2">
        {["draft", "rejected"].includes(a.status) && <button className="btn btn-ghost" onClick={() => review.mutate("submit")}>Submit for review</button>}
        {["draft", "in_review"].includes(a.status) && <button className="btn btn-primary" onClick={() => review.mutate("approve")}>Approve</button>}
        {a.status === "approved" && <button className="btn btn-verify" onClick={() => review.mutate("publish")}>Publish</button>}
        {["in_review", "draft", "approved"].includes(a.status) && <button className="btn btn-ghost" onClick={() => review.mutate("reject")}>Reject</button>}
        {a.status === "published" && <button className="btn btn-ghost" onClick={() => review.mutate("archive")}>Archive</button>}
      </div></div>
    {a.reviews.length > 0 && <ol className="mt-4 text-sm text-slate">{a.reviews.map((rv, i) => <li key={i}>{fmtDate(rv.at)} · {rv.decision}{rv.comment && ` — ${rv.comment}`}</li>)}</ol>}
  </div>;
}
