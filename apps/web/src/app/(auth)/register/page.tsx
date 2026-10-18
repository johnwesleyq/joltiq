"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { useQueryClient } from "@tanstack/react-query";
import { ErrorNote, Field } from "@/components/ui";
import { api, type Me } from "@/lib/api";

const schema = z.object({
  name: z.string().min(1, "Enter your name"), email: z.string().email("Enter a valid email"),
  organization_name: z.string().min(2, "Enter your company name"), password: z.string().min(12, "Use at least 12 characters"),
});

export default function Register() {
  const router = useRouter();
  const qc = useQueryClient();
  const [err, setErr] = useState<unknown>(null);
  const f = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema) });
  const onSubmit = f.handleSubmit(async (v) => {
    setErr(null);
    try { qc.setQueryData(["me"], await api<Me>("/auth/register", { json: v })); router.replace("/console"); } catch (e) { setErr(e); }
  });
  const e = f.formState.errors;
  return <form onSubmit={onSubmit} className="space-y-4" noValidate>
    <h1 className="text-2xl font-bold">Start your trial</h1>
    <p className="text-sm text-slate">14 days, no card required. You&apos;ll be the owner of your organization.</p>
    <ErrorNote error={err} />
    <Field label="Your name" error={e.name?.message}><input className="input" autoComplete="name" {...f.register("name")} /></Field>
    <Field label="Work email" error={e.email?.message}><input className="input" type="email" autoComplete="email" {...f.register("email")} /></Field>
    <Field label="Company" error={e.organization_name?.message}><input className="input" autoComplete="organization" {...f.register("organization_name")} /></Field>
    <Field label="Password" error={e.password?.message} hint="At least 12 characters"><input className="input" type="password" autoComplete="new-password" {...f.register("password")} /></Field>
    <button className="btn btn-primary w-full justify-center" disabled={f.formState.isSubmitting}>Create organization</button>
    <p className="text-sm text-slate">Already have an account? <Link href="/login" className="underline">Sign in</Link></p>
  </form>;
}
