"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ErrorNote, Field, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { useAct, useMe } from "@/lib/hooks";
export default function Account() {
  const me = useMe();
  const router = useRouter();
  const [cur, setCur] = useState(""); const [next, setNext] = useState(""); const [code, setCode] = useState("");
  const resend = useAct(() => api("/auth/resend-verification", { method: "POST" }));
  const change = useAct(() => api("/auth/password/change", { json: { current_password: cur, new_password: next } }));
  const setup = useAct(() => api<{ secret: string; otpauth_uri: string }>("/auth/mfa/setup", { method: "POST" }));
  const enable = useAct(() => api("/auth/mfa/enable", { json: { code } }), [["me"]]);
  const disable = useAct(() => api("/auth/mfa/disable", { json: { code } }), [["me"]]);
  const logoutAll = useAct(() => api("/auth/logout-all", { method: "POST" }));
  const u = me.data?.user;
  return <div className="max-w-[640px] space-y-10"><PageHead title="Your account" />
    <section><h2 className="text-lg font-bold">Email</h2><p>{u?.email} {u?.email_verified ? <span className="text-verify">· confirmed</span> : <span className="text-stop">· not confirmed</span>}</p>
      {!u?.email_verified && <button className="btn btn-ghost mt-2" onClick={() => resend.mutate(undefined)}>{resend.isSuccess ? "Sent — check your inbox" : "Resend confirmation email"}</button>}<ErrorNote error={resend.error} /></section>
    <section className="space-y-3"><h2 className="text-lg font-bold">Password</h2><ErrorNote error={change.error} />{change.isSuccess && <p className="text-verify">Password changed. Other sessions were signed out.</p>}
      <Field label="Current password"><input className="input" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} /></Field>
      <Field label="New password" hint="At least 12 characters"><input className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /></Field>
      <button className="btn btn-primary" onClick={() => change.mutate(undefined)}>Change password</button></section>
    <section className="space-y-3"><h2 className="text-lg font-bold">Two-step sign-in</h2><ErrorNote error={setup.error || enable.error || disable.error} />
      {u?.mfa_enabled ? <><p>On. You&apos;ll be asked for an authenticator code when signing in.</p>
        <Field label="Code to turn off"><input className="input max-w-[180px]" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} /></Field>
        <button className="btn btn-ghost" onClick={() => disable.mutate(undefined)}>Turn off</button></> :
       setup.data ? <><p className="text-sm">Add this key to your authenticator app, then enter the 6-digit code.</p><code className="block break-all rounded bg-paper p-2">{setup.data.secret}</code>
        <Field label="Code"><input className="input max-w-[180px]" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} /></Field>
        <button className="btn btn-primary" onClick={() => enable.mutate(undefined)}>Turn on</button></> :
       <button className="btn btn-ghost" onClick={() => setup.mutate(undefined)}>Set up an authenticator app</button>}</section>
    <section><h2 className="text-lg font-bold">Sessions</h2><button className="btn btn-ghost mt-2" onClick={() => logoutAll.mutate(undefined, { onSuccess: () => router.replace("/login") })}>Sign out everywhere</button></section>
  </div>;
}
