"use client";
import clsx from "clsx";
import React, { useEffect, useState, type ReactElement, type ReactNode } from "react";

export function useNow(ms = 15_000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(timer); }, [ms]);
  return now;
}

export function span(ms: number) {
  const minutes = Math.max(0, Math.round(Math.abs(ms) / 60_000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours}h` : `${Math.round(hours / 24)}d`;
}

const RISK_TONE: Record<string, string> = {
  READ_ONLY: "bg-paper text-slate", LOW_RISK: "bg-ion/15 text-ion", SENSITIVE: "bg-jolt-soft text-ink",
  PRIVILEGED: "bg-stop-soft text-stop", DESTRUCTIVE: "bg-stop text-white",
};
export function RiskBadge({ risk }: { risk: string }) {
  const label = risk.replaceAll("_", " ").toLowerCase().replace(/^\w/, (value) => value.toUpperCase());
  return <span className={clsx("inline-flex rounded-full px-2.5 py-0.5 text-sm font-semibold", RISK_TONE[risk] ?? "bg-paper")}>{label}</span>;
}

export function ParamsView({ params }: { params: Record<string, unknown> }) {
  const rows = Object.entries(params ?? {});
  if (!rows.length) return <p className="text-sm text-slate">No parameters.</p>;
  return <dl className="overflow-hidden rounded-xl border border-line text-sm">{rows.map(([key, value]) =>
    <div key={key} className="grid grid-cols-[minmax(120px,1fr)_2fr] border-b border-line last:border-0">
      <dt className="bg-paper px-3 py-2 font-medium text-slate">{key.replaceAll("_", " ")}</dt>
      <dd className="break-all px-3 py-2 font-mono text-xs">{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd>
    </div>)}</dl>;
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={clsx("input min-h-20", props.className)} />;
}

export function ConfirmDialog({ trigger, title, description, confirmLabel, destructive = false, onConfirm }: {
  trigger: ReactElement<{ onClick?: React.MouseEventHandler }>;
  title: string; description?: ReactNode; confirmLabel: string; destructive?: boolean; onConfirm: () => void;
}) {
  const [open, setOpen] = useState(false);
  const opener = React.cloneElement(trigger, { onClick: (event) => { trigger.props.onClick?.(event); setOpen(true); } });
  return <>{opener}{open && <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onMouseDown={() => setOpen(false)}>
    <section role="dialog" aria-modal="true" aria-labelledby="confirm-title" className="w-full max-w-md rounded-2xl border border-line bg-surface p-6 shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
      <h2 id="confirm-title" className="text-lg font-semibold">{title}</h2>
      {description && <div className="mt-2 text-sm text-slate">{description}</div>}
      <div className="mt-6 flex justify-end gap-2">
        <button type="button" className="btn btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
        <button type="button" className={destructive ? "btn bg-stop text-white" : "btn btn-primary"} onClick={() => { setOpen(false); onConfirm(); }}>{confirmLabel}</button>
      </div>
    </section>
  </div>}</>;
}
