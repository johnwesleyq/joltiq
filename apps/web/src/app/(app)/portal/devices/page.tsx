"use client";
import { Badge, Empty, ErrorNote, Loading, PageHead } from "@/components/ui";
import { api, fmtDate, type Page } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";
type D = { id: string; hostname: string; os_name: string; os_version: string; agent_version: string; status: string; online: boolean; last_seen_at: string | null };
export default function Devices() {
  const r = useApi<Page<D>>(["devices"], "/devices", { refetchInterval: 10000 });
  const code = useAct(() => api<{ code: string; expires_at: string; server_url: string }>("/devices/enrollment-codes", { json: {} }));
  const revoke = useAct((id: string) => api(`/devices/${id}/revoke`, { method: "POST" }), [["devices"]]);
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
        <td>{d.status === "active" && <button className="btn btn-ghost px-2 py-1 text-xs" onClick={() => confirm(`Revoke ${d.hostname}? It will stop working until re-enrolled.`) && revoke.mutate(d.id)}>Revoke</button>}</td></tr>)}</tbody></table></div>}</div>;
}
