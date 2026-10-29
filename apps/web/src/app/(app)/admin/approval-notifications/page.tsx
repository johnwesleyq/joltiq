"use client";
import Link from "next/link";
import { useState } from "react";
import { ErrorNote, Loading, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";
type Ch = { in_app: boolean; email: boolean; slack: boolean; teams: boolean };
type S = { reminder_minutes: number; email_rate_per_hour: number; slack_rate_per_hour: number; teams_rate_per_hour: number; channels: Record<"approval_requested" | "approval_reminder" | "approval_outcome", Ch>; slack_connected: boolean; teams_connected: boolean };
const EVENTS: [keyof S["channels"], string, string][] = [["approval_requested", "Approval requested", "Sent to every eligible approver (never the requester)."],
  ["approval_reminder", "Reminder before expiry", "Sent once, the lead time below before the approval expires."],
  ["approval_outcome", "Outcome", "Approved, denied or expired. Sent to the requester."]];
const CH: [keyof Ch, string][] = [["in_app", "In-app"], ["email", "Email"], ["slack", "Slack"], ["teams", "Teams"]];

export default function ApprovalNotifications() {
  const r = useApi<S>(["approval-notify"], "/admin/approval-notifications");
  if (!r.data) return <Loading />;
  return <Form data={r.data} />;
}

function Form({ data }: { data: S }) {
  const [rem, setRem] = useState(String(data.reminder_minutes)); const [cap, setCap] = useState(String(data.email_rate_per_hour));
  const [scap, setScap] = useState(String(data.slack_rate_per_hour)); const [tcap, setTcap] = useState(String(data.teams_rate_per_hour)); const [ch, setCh] = useState(data.channels);
  const [local, setLocal] = useState<string | null>(null);
  const save = useAct(() => api("/admin/approval-notifications", { method: "PUT", json: { reminder_minutes: Number(rem), email_rate_per_hour: Number(cap),
    slack_rate_per_hour: Number(scap), teams_rate_per_hour: Number(tcap), channels: ch } }), [["approval-notify"]]);
  const submit = () => {
    const n = (v: string, lo: number, hi: number) => /^\d+$/.test(v) && Number(v) >= lo && Number(v) <= hi;
    if (!n(rem, 1, 240)) return setLocal("Reminder lead time must be 1–240 minutes.");
    if (!n(cap, 1, 500)) return setLocal("Email cap must be 1–500 per hour.");
    if (!n(scap, 1, 1000)) return setLocal("Slack cap must be 1–1000 per hour.");
    if (!n(tcap, 1, 1000)) return setLocal("Teams cap must be 1–1000 per hour.");
    if (!ch.approval_requested.in_app && !ch.approval_requested.email) return setLocal("Approval requests must go in-app or by email so each approver gets them.");
    setLocal(null); save.mutate(undefined);
  };
  return <div><PageHead title="Approval alerts" description="How approvers and requesters hear about approvals. People can mute email or in-app for themselves under Notifications; these switches apply to the whole organization. Changes are audited." />
    <form className="panel max-w-3xl space-y-5 p-5" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid gap-4 md:grid-cols-4">
        <label className="field"><span className="text-sm font-semibold">Reminder lead time (minutes)</span><input className="input" inputMode="numeric" aria-label="Reminder lead time" value={rem} onChange={(e) => setRem(e.target.value)} /></label>
        <label className="field"><span className="text-sm font-semibold">Email cap per person per hour</span><input className="input" inputMode="numeric" aria-label="Email cap" value={cap} onChange={(e) => setCap(e.target.value)} /></label>
        <label className="field"><span className="text-sm font-semibold">Slack cap per channel per hour</span><input className="input" inputMode="numeric" aria-label="Slack cap" value={scap} onChange={(e) => setScap(e.target.value)} /></label>
        <label className="field"><span className="text-sm font-semibold">Teams cap per channel per hour</span><input className="input" inputMode="numeric" aria-label="Teams cap" value={tcap} onChange={(e) => setTcap(e.target.value)} /></label>
      </div>
      <table className="table" data-testid="channel-matrix"><thead><tr><th>Event</th>{CH.map(([k, l]) => <th key={k}>{l}</th>)}</tr></thead>
        <tbody>{EVENTS.map(([k, l, d]) => <tr key={k}><td><b>{l}</b><div className="text-xs text-slate">{d}</div></td>
          {CH.map(([c, cl]) => <td key={c}><input type="checkbox" aria-label={`${l} ${cl}`} checked={ch[k][c]} onChange={(e) => setCh({ ...ch, [k]: { ...ch[k], [c]: e.target.checked } })} /></td>)}</tr>)}</tbody></table>
      {!data.slack_connected && <p className="text-sm text-slate">Slack isn&apos;t connected, so the Slack switches have no effect until you <Link className="underline" href="/admin/integrations">connect Slack</Link>.</p>}
      {!data.teams_connected && <p className="text-sm text-slate">Teams isn&apos;t connected, so the Teams switches have no effect until you <Link className="underline" href="/admin/integrations">connect Teams</Link>.</p>}
      {local && <p role="alert" className="text-sm text-stop">{local}</p>}
      <ErrorNote error={save.error} />\n      {save.isSuccess && <p className="text-sm text-verify">Approval alert settings saved</p>}
      <button className="btn btn-primary">Save approval alerts</button>
    </form></div>;
}

