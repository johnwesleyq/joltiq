"use client";
import { useState } from "react";
import { Empty, ErrorNote, Loading, PageHead } from "@/components/ui";
import { api, fmtDate, type Page } from "@/lib/api";
import { useAct, useApi, useMe } from "@/lib/hooks";
type U = { user_id: string; email: string; name: string; role: string; mfa_enabled: boolean };
type I = { id: string; email: string; role: string; expires_at: string; expired: boolean };
const ROLES = ["technician", "admin", "auditor", "org_owner"];
export default function Users() {
  const me = useMe();
  const users = useApi<Page<U>>(["users"], "/users?page_size=200");
  const invites = useApi<I[]>(["invites"], "/users/invitations");
  const [email, setEmail] = useState(""); const [role, setRole] = useState("technician");
  const invite = useAct(() => api<{ dev_accept_link?: string }>("/users/invitations", { json: { email, role } }), [["invites"]]);
  const setRoleM = useAct((v: { id: string; role: string }) => api(`/users/${v.id}/role`, { method: "PATCH", json: { role: v.role } }), [["users"]]);
  const remove = useAct((id: string) => api(`/users/${id}`, { method: "DELETE" }), [["users"]]);
  const revoke = useAct((id: string) => api(`/users/invitations/${id}`, { method: "DELETE" }), [["invites"]]);
  return <div><PageHead title="People" />
    <form className="panel mb-6 flex flex-wrap items-end gap-2 p-4" onSubmit={(e) => { e.preventDefault(); invite.mutate(undefined, { onSuccess: () => setEmail("") }); }}>
      <label className="field grow"><span className="text-sm font-semibold">Invite by email</span><input className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
      <select className="input max-w-[160px]" value={role} onChange={(e) => setRole(e.target.value)} aria-label="Role">{ROLES.map((r) => <option key={r} value={r}>{r.replace("_", " ")}</option>)}</select>
      <button className="btn btn-primary">Send invitation</button>
      <div className="w-full"><ErrorNote error={invite.error || setRoleM.error || remove.error} />{invite.isSuccess && <p className="text-sm text-verify">Invitation sent.</p>}</div>
    </form>
    <p className="mb-2 text-sm text-slate">Customers are invited from their customer page so they&apos;re linked to the right account.</p>
    {users.isLoading ? <Loading /> : <div className="panel overflow-x-auto"><table className="table"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>2-step</th><th /></tr></thead>
      <tbody>{users.data?.items.map((u) => <tr key={u.user_id}><td>{u.name}</td><td>{u.email}</td>
        <td>{u.role === "customer" || u.user_id === me.data?.user.id ? u.role : <select className="input py-1" value={u.role} onChange={(e) => setRoleM.mutate({ id: u.user_id, role: e.target.value })} aria-label={`Role for ${u.email}`}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select>}</td>
        <td>{u.mfa_enabled ? "On" : "Off"}</td>
        <td>{u.user_id !== me.data?.user.id && u.role !== "org_owner" && <button className="btn btn-ghost px-2 py-1 text-xs" onClick={() => confirm(`Remove ${u.email}?`) && remove.mutate(u.user_id)}>Remove</button>}</td></tr>)}</tbody></table></div>}
    <h2 className="mb-2 mt-8 text-lg font-bold">Pending invitations</h2>
    {!invites.data?.length ? <Empty>No pending invitations.</Empty> : <div className="panel"><table className="table"><tbody>{invites.data.map((i) => <tr key={i.id}><td>{i.email}</td><td>{i.role}</td>
      <td className={i.expired ? "text-stop" : "text-slate"}>{i.expired ? "Expired" : `Expires ${fmtDate(i.expires_at)}`}</td><td><button className="btn btn-ghost px-2 py-1 text-xs" onClick={() => revoke.mutate(i.id)}>Revoke</button></td></tr>)}</tbody></table></div>}
  </div>;
}
