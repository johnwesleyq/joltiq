"use client";
import Link from "next/link";
import { useState } from "react";
import { Badge, Empty, ErrorNote, Loading, PageHead } from "@/components/ui";
import { api, fmtDate, type Page } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";
type D = { id: string; title: string; source_type: string; status: string; error: string | null; version: number; visibility: string[]; chunks: number | null; embedder: string | null; semantic: boolean | null; updated_at: string };
type A = { id: string; title: string; status: string; verified_resolution: boolean; executable: boolean; drafted_by_ai: boolean; updated_at: string };
type H = { title: string; citation: string; text: string; score: number };
export default function Knowledge() {
  const [tab, setTab] = useState<"docs" | "import" | "articles" | "search">("docs");
  const [unresolved, setUnresolved] = useState(false);
  type R = { imported: number; skipped: number; duplicates: number; errors: string[] };
  const imp = useAct(({ f, kind }: { f: File; kind: "tickets" | "articles" }) => { const fd = new FormData(); fd.append("file", f); fd.append("kind", kind);
    fd.append("include_unresolved", String(unresolved)); if (customerVisible) fd.append("visibility", "customer,technician");
    return api<R>("/knowledge/import", { method: "POST", body: fd }); }, [["kdocs"]]);
  const docs = useApi<Page<D>>(["kdocs"], "/knowledge/documents?page_size=100", { refetchInterval: 5000 });
  const arts = useApi<Page<A>>(["karts"], "/knowledge/articles?page_size=100");
  const [url, setUrl] = useState(""); const [q, setQ] = useState(""); const [customerVisible, setCV] = useState(false);
  const upload = useAct((f: File) => { const fd = new FormData(); fd.append("file", f); fd.append("visibility", customerVisible ? "customer,technician" : ""); return api("/knowledge/documents", { method: "POST", body: fd }); }, [["kdocs"]]);
  const addUrl = useAct(() => api("/knowledge/documents/url", { json: { url, visibility: customerVisible ? ["customer", "technician"] : undefined } }), [["kdocs"]]);
  const del = useAct((id: string) => api(`/knowledge/documents/${id}`, { method: "DELETE" }), [["kdocs"]]);
  const reindex = useAct((id: string) => api(`/knowledge/documents/${id}/reindex`, { method: "POST" }), [["kdocs"]]);
  const search = useAct(() => api<{ results: H[]; retrieval: { vector: string; semantic_model: boolean } }>("/knowledge/search", { json: { query: q } }));
  return <div><PageHead title="Knowledge" />
    <div className="mb-5 flex gap-2">{(["docs", "import", "articles", "search"] as const).map((t) => <button key={t} className={tab === t ? "btn btn-primary px-3 py-1" : "btn btn-ghost px-3 py-1"} onClick={() => setTab(t)}>{t === "docs" ? "Sources" : t === "import" ? "Import history" : t === "articles" ? "Articles" : "Test search"}</button>)}</div>
    <ErrorNote error={upload.error || addUrl.error || del.error || imp.error} />
    {tab === "import" && <div className="grid max-w-[900px] gap-5 md:grid-cols-2">
      <div className="panel space-y-3 p-5"><h2 className="text-lg font-bold">Past tickets</h2>
        <p className="text-sm text-slate">Export from Zendesk, Freshdesk, Intercom, HubSpot Service Hub, Jira or any spreadsheet as CSV or JSON (a ZIP of CSVs is fine; Excel files aren’t supported yet). Columns are matched by name:
          subject/title, description/body, resolution/solution, status, tags. Resolved tickets become fixes JoltIQ can learn from. Names, emails, phone numbers and other personal data are redacted on import.</p>
        <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={unresolved} onChange={(e) => setUnresolved(e.target.checked)} /> Also import unresolved tickets</label>
        <label className="btn btn-primary cursor-pointer">Choose CSV or JSON<input type="file" className="sr-only" accept=".csv,.json,.tsv,.txt,.zip" onChange={(e) => e.target.files?.[0] && imp.mutate({ f: e.target.files[0], kind: "tickets" })} /></label></div>
      <div className="panel space-y-3 p-5"><h2 className="text-lg font-bold">Help-center articles</h2>
        <p className="text-sm text-slate">A ZIP of your articles or runbooks (Markdown, HTML, text, PDF, Word). Most help centers can export to a ZIP.</p>
        <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={customerVisible} onChange={(e) => setCV(e.target.checked)} /> Customers can see them</label>
        <label className="btn btn-primary cursor-pointer">Choose ZIP<input type="file" className="sr-only" accept=".zip" onChange={(e) => e.target.files?.[0] && imp.mutate({ f: e.target.files[0], kind: "articles" })} /></label></div>
      {imp.isPending && <p className="pulse text-slate md:col-span-2">Importing…</p>}
      {imp.data && <div className="panel p-4 text-sm md:col-span-2" role="status"><b>Imported {imp.data.imported}</b> · skipped {imp.data.skipped} · duplicates {imp.data.duplicates}
        {imp.data.errors.length > 0 && <ul className="mt-2 list-disc pl-5 text-stop">{imp.data.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>}
        <p className="mt-1 text-slate">Indexing runs in the background; items appear under Sources as they finish.</p></div>}
    </div>}
    {tab === "docs" && <>
      <div className="panel mb-5 flex flex-wrap items-end gap-3 p-4">
        <label className="btn btn-primary cursor-pointer">Upload file<input type="file" className="sr-only" accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.html,.vtt,.srt" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} /></label>
        <form className="flex grow gap-2" onSubmit={(e) => { e.preventDefault(); addUrl.mutate(undefined, { onSuccess: () => setUrl("") }); }}>
          <input className="input" type="url" placeholder="https://learn.microsoft.com/…" value={url} onChange={(e) => setUrl(e.target.value)} aria-label="Document URL" /><button className="btn btn-ghost">Add URL</button></form>
        <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={customerVisible} onChange={(e) => setCV(e.target.checked)} /> Customers can see it</label>
        <p className="w-full text-xs text-slate">PDF, Word, Excel, CSV, Markdown, text, HTML and call transcripts. URLs must be on the approved domain list.</p></div>
      {docs.isLoading ? <Loading /> : !docs.data?.items.length ? <Empty>Upload runbooks, SOPs or manuals so the assistant can cite them.</Empty> :
      <div className="panel overflow-x-auto"><table className="table"><thead><tr><th>Title</th><th>Type</th><th>Status</th><th>Chunks</th><th>Visible to</th><th>Updated</th><th /></tr></thead>
        <tbody>{docs.data.items.map((d) => <tr key={d.id}><td className="font-medium">{d.title}{d.error && <div className="text-xs text-stop">{d.error}</div>}</td><td>{d.source_type}</td><td><Badge value={d.status} /></td>
          <td>{d.chunks ?? "—"}</td><td className="text-xs">{d.visibility.includes("customer") ? "customers + staff" : "staff"}</td><td className="text-slate">{fmtDate(d.updated_at)}</td>
          <td className="whitespace-nowrap"><button className="btn btn-ghost px-2 py-1 text-xs" onClick={() => reindex.mutate(d.id)}>Reindex</button> <button className="btn btn-ghost px-2 py-1 text-xs" onClick={() => confirm(`Delete ${d.title}?`) && del.mutate(d.id)}>Delete</button></td></tr>)}</tbody></table></div>}</>}
    {tab === "articles" && (!arts.data?.items.length ? <Empty>Articles are drafted after verified resolutions and wait here for review.</Empty> :
      <div className="panel overflow-x-auto"><table className="table"><thead><tr><th>Article</th><th>Status</th><th>Origin</th><th>Runnable</th><th>Updated</th></tr></thead>
        <tbody>{arts.data.items.map((a) => <tr key={a.id}><td><Link className="font-medium hover:underline" href={`/admin/knowledge/articles/${a.id}`}>{a.title}</Link></td><td><Badge value={a.status === "published" ? "VERIFIED" : a.status === "rejected" ? "FAILED" : "PENDING"} label={a.status} /></td>
          <td className="text-sm">{a.drafted_by_ai ? "AI draft" : "Written by staff"}{a.verified_resolution && " · verified fix"}</td><td>{a.executable ? "Yes" : "No"}</td><td className="text-slate">{fmtDate(a.updated_at)}</td></tr>)}</tbody></table></div>)}
    {tab === "search" && <div className="max-w-[760px]"><form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); search.mutate(undefined); }}>
      <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search as the assistant would" aria-label="Test query" /><button className="btn btn-primary">Search</button></form>
      {search.data && <p className="mt-2 text-xs text-slate">Keyword + {search.data.retrieval.vector}{!search.data.retrieval.semantic_model && " (lexical embedding; add an OpenAI key for semantic search)"}</p>}
      <ol className="mt-4 space-y-3">{search.data?.results.map((h, i) => <li key={i} className="panel p-3"><div className="text-sm font-medium">{h.citation}</div><p className="mt-1 text-sm text-slate">{h.text.slice(0, 400)}</p></li>)}</ol></div>}
  </div>;
}
