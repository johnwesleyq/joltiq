import Link from "next/link";
import Core from "@/components/Core";
import type { ReactNode } from "react";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return <div className="grid min-h-screen place-items-center bg-paper px-4">
    <div className="w-full max-w-[420px]">
      <Link href="/" className="mb-6 flex items-center gap-2"><Core size={28} />
        <span className="font-display text-xl font-bold">JoltIQ</span></Link>
      <div className="panel p-7">{children}</div>
    </div>
  </div>;
}
