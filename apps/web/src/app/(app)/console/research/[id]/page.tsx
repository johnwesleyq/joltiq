"use client";
import { use } from "react";
import { Badge, ErrorNote, Loading, PageHead } from "@/components/ui";
import { fmtDate } from "@/lib/api";
import { useApi } from "@/lib/hooks";
type S = { id: string; url: string; domain: string; title: string; retrieved_at: string; excerpt: string; source_kind: string; reliability: number; relevance: number; applicable_versions: string[]; flags: string[] };
type R = { query: string; provider: string; live: boolean; status: string; error: string | null; redactions: string[]; sources: S[]; findings: { kind: string; statement: string; confidence: number }[] };
export default function ResearchDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const r = useApi<R>(["research", id], `/research/${id}`);
  if (r.isLoading) return <Loading />;
  if (!r.data) return <ErrorNote error={r.error} />;
  const d = r.data;
  return <div><PageHead title="Research results" back={{ href: "/console/research", label: "Research" }}><Badge value={d.status === "ok" ? "VERIFIED" : "FAILED"} label={d.status} /></PageHead>
    <p className="text-sm text-slate">Query sent: <code>{d.query}</code> · provider {d.provider}{!d.live && " (test fixture)"}{d.redactions.length > 0 && ` · removed: ${d.redactions.join(", ")}`}</p>
    {d.error && <p className="mt-2 text-stop">{d.error}</p>}
    {d.findings.length > 0 && <div className="mt-6 grid gap-6 md:grid-cols-2">
      <div><h2 className="font-bold">Evidence (vendor-confirmed)</h2><ul className="mt-1 list-disc pl-5">{d.findings.filter((f) => f.kind === "evidence").map((f, i) => <li key={i}>{f.statement}</li>)}</ul></div>
      <div><h2 className="font-bold">Hypotheses (unconfirmed)</h2><ul className="mt-1 list-disc pl-5 text-slate">{d.findings.filter((f) => f.kind === "hypothesis").map((f, i) => <li key={i}>{f.statement}</li>)}</ul></div></div>}
    <h2 className="mt-8 font-bold">Sources</h2>
    <div className="mt-2 space-y-3">{d.sources.map((s) => <div key={s.id} className="panel p-4">
      <a className="font-medium underline" href={s.url} target="_blank" rel="noopener noreferrer nofollow">{s.title || s.url}</a>
      <div className="text-xs text-slate">{s.domain} · {s.source_kind} · reliability {Math.round(s.reliability * 100)}% · relevance {Math.round(s.relevance * 100)}% · retrieved {fmtDate(s.retrieved_at)}{s.applicable_versions.length > 0 && ` · ${s.applicable_versions.join(", ")}`}</div>
      <p className="mt-2 text-sm">{s.excerpt}</p>
      {s.flags.length > 0 && <p className="mt-1 text-xs text-stop">{s.flags.map((f) => f.replaceAll("_", " ")).join(" · ")}</p>}</div>)}</div></div>;
}
