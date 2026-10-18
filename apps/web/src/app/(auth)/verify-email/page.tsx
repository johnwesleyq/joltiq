"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { api } from "@/lib/api";

function V() {
  const token = useSearchParams().get("token") || "";
  const [state, setState] = useState<"working" | "ok" | "fail">("working");
  const [msg, setMsg] = useState("");
  useEffect(() => { api("/auth/verify-email", { json: { token } }).then(() => setState("ok")).catch((e) => { setMsg(e.message); setState("fail"); }); }, [token]);
  if (state === "working") return <p className="pulse">Confirming your email…</p>;
  return <div className="space-y-3"><h1 className="text-2xl font-bold">{state === "ok" ? "Email confirmed" : "Link didn't work"}</h1>
    <p className="text-slate">{state === "ok" ? "Thanks. You can continue to your workspace." : `${msg} Request a new link from your account page.`}</p>
    <Link className="btn btn-primary" href="/portal">Continue</Link></div>;
}
export default function VerifyEmail() { return <Suspense><V /></Suspense>; }
