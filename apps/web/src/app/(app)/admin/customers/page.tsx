"use client";
import { useState } from "react";
import { Empty, ErrorNote, Loading, PageHead } from "@/components/ui";
import { api, type Page } from "@/lib/api";
import { useAct, useApi } from "@/lib/hooks";
type C = { id: string; name: string; domain: string | null; notes: string };
export default function Customers() {
  const [q, setQ] = useState("");
  const r = useApi<Page<C>>(["customers", q], `/customers?page_size=100${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  const [name, setName] = useState(""); const [domain, setDomain] = useState("");
  const [sel, setSel] = useState<string | null>(null); const [email, setEmail] = useState("");
  const create = useAct(() => api("/customers", { json: { name, domain: domain || null } }), [["customers"]]);
  const invite = useAct(() => api("/users/invitations", { json: { email, role: "customer", customer_id: sel } }));
  return <div><PageHead title="Customers" />
    <form className="panel mb-6 flex flex-wrap items-end gap-2 p-4" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined, { onSuccess: () => { setName(""); setDomain(""); } }); }}>
      <label className="field grow"><span className="text-sm font-semibold">Company</span><input className="input" required value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field"><span className="text-sm font-semibold">Email domain</span><input className="input" placeholder="acme.com" value={domain} onChange={(e) => setDomain(e.target.value)} /></label>
      <button className="btn btn-primary">Add customer</button><div className="w-full"><ErrorNote error={create.error} /></div></form>
    <input className="input mb-3 max-w-[300px]" placeholder="Search customers" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search customers" />
    {r.isLoading ? <Loading /> : !r.data?.items.length ? <Empty>No customers yet.</Empty> :
    <div className="panel overflow-x-auto"><table className="table"><thead><tr><th>Company</th><th>Domain</th><th /></tr></thead>
      <tbody>{r.data.items.map((c) => <tr key={c.id}><td className="font-medium">{c.name}</td><td>{c.domain ?? "—"}</td>
        <td>{sel === c.id ? <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); invite.mutate(undefined, { onSuccess: () => { setEmail(""); setSel(null); } }); }}>
          <input className="input py-1" type="email" required placeholder="person@company.com" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="Customer email" /><button className="btn btn-primary px-2 py-1 text-xs">Invite</button></form>
          : <button className="btn btn-ghost px-2 py-1 text-xs" onClick={() => setSel(c.id)}>Invite a person</button>}</td></tr>)}</tbody></table></div>}
    <ErrorNote error={invite.error} />{invite.isSuccess && <p className="mt-2 text-sm text-verify">Invitation sent.</p>}</div>;
}
