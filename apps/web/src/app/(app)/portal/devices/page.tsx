"use client";
import { useState } from "react";
import { Badge, Empty, ErrorNote, Loading, PageHead } from "@/components/ui";
import { api, fmtDate, STAFF, type Page } from "@/lib/api";
import { useAct, useApi, useMe } from "@/lib/hooks";
type D = { id: string; hostname: string; os_name: string; os_version: string; agent_version: string; status: string; online: boolean; last_seen_at: string | null };
type RemoteSession = { id: string; state: string; reason: string; workspace_path: string; transport_url: string | null; launch_url: string | null; created_at: string; ended_at: string | null };
type RemoteStatus = { enabled: boolean; configured: boolean; provider: string; node_id: string | null; active_session: RemoteSession | null };

function RemoteSupport({ device }: { device: D }) {
  const status = useApi<RemoteStatus>(["remote-access", device.id], `/devices/${device.id}/remote-access`);
  const [nodeId, setNodeId] = useState("");
  const [reason, setReason] = useState("");
  const bind = useAct(() => api<RemoteStatus>(`/devices/${device.id}/remote-access`, { method: "PUT", json: { node_id: nodeId } }), [["remote-access", device.id]]);
  const start = useAct(() => api<RemoteSession>(`/devices/${device.id}/remote-access/sessions`, { json: { reason } }), [["remote-access", device.id]]);
  const end = useAct((id: string) => api<RemoteSession>(`/devices/${device.id}/remote-access/sessions/${id}/end`, { method: "POST" }), [["remote-access", device.id]]);
  const open = async () => {
    if (reason.trim().length < 5) return;
    const popup = window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    try {
      const session = await start.mutateAsync(undefined);
      if (session.workspace_path) {
        if (popup) popup.location.href = session.workspace_path;
        else window.location.assign(session.workspace_path);
      } else popup?.close();
    } catch { popup?.close(); }
  };
  if (status.isLoading) return <span className="text-xs text-slate">Checking remote support…</span>;
  const value = status.data;
  return <details className="min-w-[260px] text-left">
    <summary className="btn btn-ghost cursor-pointer px-2 py-1 text-xs">Remote support</summary>
    <div className="mt-2 space-y-2 rounded-md border border-line bg-surface p-3 shadow-sm">
      <ErrorNote error={status.error || bind.error || start.error || end.error} />
      {!value?.enabled ? <p className="text-xs text-stop">Remote support is disabled on this JoltIQ server.</p> : !value.configured ? <>
        <label className="block text-xs font-semibold">Remote support computer ID
          <input className="input mt-1 w-full" value={nodeId} onChange={(e) => setNodeId(e.target.value)} placeholder="Computer ID" />
        </label>
        <button className="btn btn-secondary w-full" disabled={nodeId.trim().length < 3 || bind.isPending} onClick={() => bind.mutate(undefined)}>Link computer</button>
      </> : value.active_session ? <>
        <p className="text-xs"><b>JoltIQ Remote Support is ready.</b> The person at the computer must still accept the approval prompt on the PC.</p>
        <a className="btn btn-secondary block text-center" href={value.active_session.workspace_path} target="_blank" rel="noreferrer">Open support workspace</a>
        <button className="btn btn-ghost w-full" disabled={end.isPending} onClick={() => end.mutate(value.active_session!.id)}>End support</button>
      </> : <>
        <label className="block text-xs font-semibold">Why access is needed
          <textarea className="input mt-1 min-h-20 w-full" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Customer requested help with…" />
        </label>
        <p className="text-[11px] text-slate">The customer must click Allow on their PC. This does not approve an automated repair.</p>
        <button className="btn btn-primary w-full" disabled={reason.trim().length < 5 || start.isPending} onClick={open}>Request remote desktop</button>
      </>}
    </div>
  </details>;
}

export default function Devices() {
  const me = useMe();
  const r = useApi<Page<D>>(["devices"], "/devices", { refetchInterval: 10000 });
  const code = useAct(() => api<{ code: string; expires_at: string; server_url: string }>("/devices/enrollment-codes", { json: {} }));
  const revoke = useAct((id: string) => api(`/devices/${id}/revoke`, { method: "POST" }), [["devices"]]);
  const isStaff = !!me.data?.active_organization && STAFF.includes(me.data.active_organization.role);
  return <div><PageHead title="Devices"><button className="btn btn-primary" onClick={() => code.mutate(undefined)}>Add a Windows PC</button></PageHead>
    <ErrorNote error={code.error || revoke.error} />
    {code.data && <div className="panel mb-6 max-w-[620px] p-5">
      <h2 className="font-bold">Enroll a computer</h2>
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm"><li>Install <b>JoltIQ Companion</b> on the Windows PC (your IT provider supplies the installer).</li>
        <li>Open it from the system tray and choose <b>Enroll this device</b>.</li><li>Enter this code. It works once and expires at {new Date(code.data.expires_at).toLocaleTimeString()}.</li></ol>
      <p className="mt-3 font-display text-3xl font-bold tracking-wider">{code.data.code}</p></div>}
    {r.isLoading ? <Loading /> : !r.data?.items.length ? <Empty>No computers enrolled yet.</Empty> :
    <div className="panel overflow-x-auto"><table className="table"><thead><tr><th>Computer</th><th>Status</th><th>OS</th><th>Companion</th><th>Last seen</th><th /></tr></thead>
      <tbody>{r.data.items.map((d) => <tr key={d.id}><td className="font-medium">{d.hostname}</td>
        <td>{d.status === "active" ? <Badge value={d.online ? "VERIFIED" : "UNKNOWN"} label={d.online ? "Online" : "Offline"} /> : <Badge value="FAILED" label="Revoked" />}</td>
        <td className="text-sm">{d.os_name} {d.os_version}</td><td>{d.agent_version}</td><td className="text-slate">{fmtDate(d.last_seen_at)}</td>
        <td><div className="flex items-start gap-2">{isStaff && d.status === "active" && <RemoteSupport device={d} />}{d.status === "active" && <button className="btn btn-ghost px-2 py-1 text-xs" onClick={() => confirm(`Revoke ${d.hostname}? It will stop working until re-enrolled.`) && revoke.mutate(d.id)}>Revoke</button>}</div></td></tr>)}</tbody></table></div>}</div>;
}
