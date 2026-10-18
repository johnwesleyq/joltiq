"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { useQueryClient } from "@tanstack/react-query";
import { ErrorNote, Field } from "@/components/ui";
import { api, ApiError, STAFF, type Me } from "@/lib/api";

const schema = z.object({ email: z.string().email("Enter a valid email"), password: z.string().min(1, "Enter your password"), totp: z.string().optional() });

function LoginForm() {
  const router = useRouter();
  const next = useSearchParams().get("next");
  const qc = useQueryClient();
  const [err, setErr] = useState<unknown>(null);
  const [needMfa, setNeedMfa] = useState(false);
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
  return <form onSubmit={onSubmit} className="space-y-4" noValidate>
    <h1 className="text-2xl font-bold">Sign in</h1>
    <ErrorNote error={err} />
    <Field label="Email" error={f.formState.errors.email?.message}><input className="input" type="email" autoComplete="email" {...f.register("email")} /></Field>
    <Field label="Password" error={f.formState.errors.password?.message}><input className="input" type="password" autoComplete="current-password" {...f.register("password")} /></Field>
    {needMfa && <Field label="Authenticator code"><input className="input" inputMode="numeric" autoComplete="one-time-code" autoFocus {...f.register("totp")} /></Field>}
    <button className="btn btn-primary w-full justify-center" disabled={f.formState.isSubmitting}>{f.formState.isSubmitting ? "Signing in…" : "Sign in"}</button>
    <div className="flex justify-between text-sm"><Link href="/forgot-password" className="text-slate hover:text-ink">Forgot password?</Link>
      <Link href="/register" className="text-slate hover:text-ink">Create an organization</Link></div>
  </form>;
}

export default function Login() { return <Suspense><LoginForm /></Suspense>; }
