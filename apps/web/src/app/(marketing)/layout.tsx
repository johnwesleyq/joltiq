import Link from "next/link";
import type { ReactNode } from "react";
import Core from "@/components/Core";

export default function MarketingLayout({ children }: { children: ReactNode }) {
  return <div className="min-h-screen">
    <header className="mx-auto flex max-w-[1180px] items-center justify-between px-6 py-5">
      <Link href="/" className="flex items-center gap-2.5"><Core size={28} /><span className="font-display text-xl font-semibold">JoltIQ</span></Link>
      <nav className="flex items-center gap-6 text-[0.95rem] text-slate">
        <Link href="/features" className="hidden hover:text-white sm:inline">How it works</Link>
        <Link href="/pricing" className="hidden hover:text-white sm:inline">Pricing</Link>
        <Link href="/security" className="hidden hover:text-white sm:inline">Security</Link>
        <Link href="/login" className="hover:text-white">Sign in</Link>
        <Link href="/register" className="btn btn-primary">Try it free</Link>
      </nav>
    </header>
    {children}
    <footer className="mt-24 border-t border-line">
      <div className="mx-auto flex max-w-[1180px] flex-wrap justify-between gap-4 px-6 py-8 text-sm text-slate">
        <span>JoltIQ is an AI support technician for MSPs, software companies and service businesses.</span>
        <span className="flex gap-5"><Link href="/security">Security</Link><Link href="/contact">Contact</Link><Link href="/pricing">Pricing</Link></span>
      </div>
    </footer>
  </div>;
}
