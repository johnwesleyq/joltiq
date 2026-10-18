"use client";
import clsx from "clsx";
import Link from "next/link";
import type { ReactNode } from "react";
import { ApiError } from "@/lib/api";

const STATUS_TONE: Record<string, string> = {
  VERIFIED: "bg-verify-soft text-verify", RESOLVED: "bg-verify-soft text-verify", CLOSED: "bg-paper text-slate", APPROVED: "bg-verify-soft text-verify",
  FAILED: "bg-stop-soft text-stop", DENIED: "bg-stop-soft text-stop", ESCALATED: "bg-stop-soft text-stop", CANCELLED: "bg-paper text-slate",
  PARTIAL: "bg-jolt-soft text-ink", PARTIALLY_RESOLVED: "bg-jolt-soft text-ink", UNKNOWN: "bg-paper text-slate",
  AI_INVESTIGATING: "bg-jolt-soft text-ink", EXECUTING: "bg-jolt-soft text-ink", VERIFYING: "bg-jolt-soft text-ink",
  AWAITING_APPROVAL: "bg-white text-ink ring-1 ring-ink", PENDING: "bg-white text-ink ring-1 ring-ink", active: "bg-jolt-soft text-ink",
  terminated: "bg-paper text-slate", pending_consent: "bg-white text-ink ring-1 ring-ink", awaiting_user_auth: "bg-white text-ink ring-1 ring-ink",
  READ_ONLY: "bg-paper text-slate", LOW_RISK: "bg-paper text-ink", SENSITIVE: "bg-jolt-soft text-ink", PRIVILEGED: "bg-stop-soft text-stop", DESTRUCTIVE: "bg-stop text-white",
  indexed: "bg-verify-soft text-verify", failed: "bg-stop-soft text-stop", connected: "bg-verify-soft text-verify", error: "bg-stop-soft text-stop",
};

export function Badge({ value, label }: { value: string; label?: string }) {
  return <span className={clsx("inline-block whitespace-nowrap rounded px-1.5 py-0.5 text-[0.78rem] font-medium", STATUS_TONE[value] ?? "bg-paper text-ink")}>
    {label ?? value.replaceAll("_", " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase())}</span>;
}

export function PageHead({ title, children, back }: { title: string; children?: ReactNode; back?: { href: string; label: string } }) {
  return <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
    <div>{back && <Link href={back.href} className="text-sm text-slate hover:text-ink">‹ {back.label}</Link>}
      <h1 className="text-[1.7rem] font-bold">{title}</h1></div>
    <div className="flex gap-2">{children}</div>
  </div>;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error as ApiError;
  return <div role="alert" className="rounded-md border border-stop/30 bg-stop-soft px-3 py-2 text-sm text-stop">
    {e.message || String(error)}{e.requestId && <span className="ml-2 opacity-70">(ref {e.requestId.slice(0, 8)})</span>}
    {e.code === "ai_not_configured" && <> — an admin can add a model provider under <Link className="underline" href="/admin/models">Models</Link>.</>}
    {e.code === "billing_not_configured" && <> — Stripe isn&apos;t set up for this deployment.</>}
  </div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line px-6 py-10 text-center text-slate">{children}</div>;
}

export function Loading() { return <div className="pulse py-8 text-slate">Loading…</div>; }

export function Field({ label, error, children, hint }: { label: string; error?: string; children: ReactNode; hint?: string }) {
  // Wrapping <label> associates the text with the control for screen readers and click-to-focus.
  return <label className="field"><span className="text-[0.85rem] font-semibold">{label}</span>{children}
    {hint && !error && <span className="text-xs font-normal text-slate">{hint}</span>}
    {error && <span role="alert" className="text-xs font-normal text-stop">{error}</span>}</label>;
}

export function Unavailable({ what, why }: { what: string; why: string }) {
  return <div className="rounded-md border border-line bg-paper px-3 py-2 text-sm text-slate"><b className="text-ink">{what} unavailable.</b> {why}</div>;
}

export function Pager({ page, hasMore, onPage }: { page: number; hasMore: boolean; onPage: (p: number) => void }) {
  return <div className="mt-3 flex items-center gap-2 text-sm">
    <button className="btn btn-ghost" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
    <span className="text-slate">Page {page}</span>
    <button className="btn btn-ghost" disabled={!hasMore} onClick={() => onPage(page + 1)}>Next</button>
  </div>;
}
