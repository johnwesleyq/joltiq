"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ErrorNote, Field, Loading } from "@/components/ui";
import { api, STAFF, type Me } from "@/lib/api";

type Info = { email: string; role: string; organization: string; account_exists: boolean };

function Accept() {
  const token = useSearchParams().get("token") || "";
  const router = useRouter();
  const qc = useQueryClient();
  const [info, setInfo] = useState<Info | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  useEffect(() => { api<Info>(`/auth/invitations/${encodeURIComponent(token)}`).then(setInfo).catch(setErr); }, [token]);
  if (!info) return err ? <ErrorNote error={err} /> : <Loading />;
  return <form className="space-y-4" onSubmit={async (e) => { e.preventDefault(); setErr(null);
      try { const me = await api<Me>("/auth/invitations/accept", { json: { token, name, password: pw } }); qc.setQueryData(["me"], me);
            router.replace(STAFF.includes(info.role) ? "/console" : "/portal"); } catch (x) { setErr(x); } }}>
    <h1 className="text-2xl font-bold">Join {info.organization}</h1>
    <p className="text-slate">You&apos;re invited as <b>{info.role.replace("_", " ")}</b> with {info.email}.</p>
    <ErrorNote error={err} />
    {!info.account_exists && <Field label="Your name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>}
    <Field label={info.account_exists ? "Your existing password" : "Choose a password"} hint={info.account_exists ? undefined : "At least 12 characters"}>
      <input className="input" type="password" required value={pw} onChange={(e) => setPw(e.target.value)} autoComplete={info.account_exists ? "current-password" : "new-password"} /></Field>
    <button className="btn btn-primary w-full justify-center">Accept invitation</button>
  </form>;
}
export default function Page() { return <Suspense><Accept /></Suspense>; }
