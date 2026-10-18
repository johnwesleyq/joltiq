"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ErrorNote, Field } from "@/components/ui";
import { api } from "@/lib/api";

function Form() {
  const token = useSearchParams().get("token") || "";
  const [pw, setPw] = useState("");
  const [done, setDone] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  if (done) return <div className="space-y-3"><h1 className="text-2xl font-bold">Password changed</h1>
    <p className="text-slate">You&apos;ve been signed out everywhere. Sign in with your new password.</p><Link href="/login" className="btn btn-primary">Sign in</Link></div>;
  return <form className="space-y-4" onSubmit={async (e) => { e.preventDefault(); setErr(null);
      try { await api("/auth/password/reset", { json: { token, password: pw } }); setDone(true); } catch (x) { setErr(x); } }}>
    <h1 className="text-2xl font-bold">Choose a new password</h1>
    <ErrorNote error={err} />
    <Field label="New password" hint="At least 12 characters"><input className="input" type="password" autoComplete="new-password" minLength={12} required value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
    <button className="btn btn-primary w-full justify-center" disabled={!token}>Change password</button>
  </form>;
}
export default function Reset() { return <Suspense><Form /></Suspense>; }
