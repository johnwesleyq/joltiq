import Link from "next/link";
import type { ReactNode } from "react";

export default function MarketingLayout({ children }: { children: ReactNode }) {
  return <div className="min-h-screen bg-white">
    <header className="mx-auto flex max-w-[1120px] items-center justify-between px-6 py-5">
      <Link href="/" className="flex items-center gap-2"><span aria-hidden className="inline-block h-3 w-3 rotate-45 bg-jolt" />
        <span className="font-display text-xl font-bold">JoltIQ</span></Link>
      <nav className="flex items-center gap-6 text-[0.95rem]">
        <Link href="/features" className="hidden sm:inline">How it works</Link>
        <Link href="/pricing" className="hidden sm:inline">Pricing</Link>
        <Link href="/security" className="hidden sm:inline">Security</Link>
        <Link href="/login">Sign in</Link>
        <Link href="/register" className="btn btn-primary">Start a trial</Link>
      </nav>
    </header>
    {children}
    <footer className="mt-24 border-t border-line">
      <div className="mx-auto flex max-w-[1120px] flex-wrap justify-between gap-4 px-6 py-8 text-sm text-slate">
        <span>JoltIQ — AI support technician for MSPs, IT teams and service businesses.</span>
        <span className="flex gap-5"><Link href="/security">Security</Link><Link href="/contact">Contact</Link><Link href="/pricing">Pricing</Link></span>
      </div>
    </footer>
  </div>;
}
