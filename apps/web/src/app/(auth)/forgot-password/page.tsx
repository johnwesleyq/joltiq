"use client";
import Link from "next/link";
import { useState } from "react";
import { ErrorNote, Field } from "@/components/ui";
import { api } from "@/lib/api";

export default function Forgot() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  if (sent) return <div className="space-y-3"><h1 className="text-2xl font-bold">Check your email</h1>
    <p className="text-slate">If an account exists for {email}, we sent a link to reset the password. It expires in 30 minutes.</p>
    <Link href="/login" className="underline">Back to sign in</Link></div>;
  return <form method="post" className="space-y-4" onSubmit={async (e) => { e.preventDefault(); setErr(null);
      try { await api("/auth/password/forgot", { json: { email } }); setSent(true); } catch (x) { setErr(x); } }}>
    <h1 className="text-2xl font-bold">Reset your password</h1>
    <ErrorNote error={err} />
    <Field label="Email"><input className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
    <button className="btn btn-primary w-full justify-center">Send reset link</button>
  </form>;
}
