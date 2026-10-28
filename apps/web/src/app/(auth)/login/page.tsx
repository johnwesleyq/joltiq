"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { useQueryClient } from "@tanstack/react-query";
import { ErrorNote, Field } from "@/components/ui";
import { api, ApiError, STAFF, type Me } from "@/lib/api";

const schema = z.object({ email: z.string().email("Enter a valid email"), password: z.string().min(1, "Enter your password"), totp: z.string().optional() });

function LoginForm() {
  const router = useRouter();
  const next = useSearchParams().get("next");
  const ssoMfa = useSearchParams().get("sso_mfa") === "1";
  const ssoError = useSearchParams().get("sso_error");
  const qc = useQueryClient();
  const [err, setErr] = useState<unknown>(null);
  const [needMfa, setNeedMfa] = useState(false);
  const [workEmail, setWorkEmail] = useState("");
  const [ssoCode, setSsoCode] = useState("");
  const [ssoBusy, setSsoBusy] = useState(false);
  const f = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema) });
  const onSubmit = f.handleSubmit(async (v) => {
    setErr(null);
    try {
      const me = await api<Me>("/auth/login", { json: { ...v, totp: v.totp || undefined } });
      qc.setQueryData(["me"], me);
      const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : null;
      router.replace(safeNext || (STAFF.includes(me.active_organization?.role ?? "") ? "/console" : "/portal"));
    } catch (e) {
      if (e instanceof ApiError && e.code === "mfa_required") { setNeedMfa(true); return; }
      setErr(e);
    }
  });
  async function continueWithWorkEmail() {
    setErr(null); setSsoBusy(true);
    try {
      const result = await api<{ available: boolean; continuation: string | null }>("/auth/sso/lookup", { json: { email: workEmail } });
      if (!result.available || !result.continuation) throw new Error("Work sign-in is not configured for that email domain.");
      window.location.assign(`/api/v1/auth/sso/start?continuation=${encodeURIComponent(result.continuation)}`);
    } catch (e) { setErr(e); setSsoBusy(false); }
  }
  async function completeSsoMfa(e: FormEvent) {
    e.preventDefault(); setErr(null); setSsoBusy(true);
    try {
      const me = await api<Me>("/auth/sso/mfa", { json: { code: ssoCode } });
      qc.setQueryData(["me"], me);
      router.replace(STAFF.includes(me.active_organization?.role ?? "") ? "/console" : "/portal");
    } catch (error) { setErr(error); setSsoBusy(false); }
  }
  if (ssoMfa) return <form onSubmit={completeSsoMfa} className="space-y-4">
    <h1 className="text-2xl font-bold">Complete work sign-in</h1>
    <p className="text-sm text-slate">Enter the code from your authenticator app.</p>
    <ErrorNote error={err} />
    <Field label="Authenticator code"><input className="input" inputMode="numeric" autoComplete="one-time-code" value={ssoCode} onChange={(e) => setSsoCode(e.target.value)} autoFocus /></Field>
    <button className="btn btn-primary w-full justify-center" disabled={ssoBusy}>{ssoBusy ? "Verifying…" : "Verify and sign in"}</button>
  </form>;
  return <form method="post" onSubmit={onSubmit} className="space-y-4" noValidate>
    <h1 className="text-2xl font-bold">Sign in</h1>
    {ssoError && <p className="rounded-lg border border-stop/30 bg-stop/10 p-3 text-sm">Work sign-in could not be completed. Try again or contact your administrator.</p>}
    <ErrorNote error={err} />
    <Field label="Email" error={f.formState.errors.email?.message}><input className="input" type="email" autoComplete="email" {...f.register("email")} /></Field>
    <Field label="Password" error={f.formState.errors.password?.message}><input className="input" type="password" autoComplete="current-password" {...f.register("password")} /></Field>
    {needMfa && <Field label="Authenticator code"><input className="input" inputMode="numeric" autoComplete="one-time-code" autoFocus {...f.register("totp")} /></Field>}
    <button className="btn btn-primary w-full justify-center" disabled={f.formState.isSubmitting}>{f.formState.isSubmitting ? "Signing in…" : "Sign in"}</button>
    <div className="flex items-center gap-3 text-xs text-slate"><span className="h-px flex-1 bg-line" />or<span className="h-px flex-1 bg-line" /></div>
    <Field label="Work email"><input className="input" type="email" autoComplete="email" value={workEmail} onChange={(e) => setWorkEmail(e.target.value)} /></Field>
    <button type="button" className="btn btn-ghost w-full justify-center" disabled={ssoBusy || !workEmail} onClick={continueWithWorkEmail}>{ssoBusy ? "Checking…" : "Continue with work email"}</button>
    <div className="flex justify-between text-sm"><Link href="/forgot-password" className="text-slate hover:text-ink">Forgot password?</Link>
      <Link href="/register" className="text-slate hover:text-ink">Create an organization</Link></div>
  </form>;
}

export default function Login() { return <Suspense><LoginForm /></Suspense>; }
