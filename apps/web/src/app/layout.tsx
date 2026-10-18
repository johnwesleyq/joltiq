import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import Providers from "./providers";

const PRODUCT = process.env.NEXT_PUBLIC_PRODUCT_NAME || "JoltIQ";

export const metadata: Metadata = {
  title: { default: `${PRODUCT} — the AI support technician that proves the fix`, template: `%s · ${PRODUCT}` },
  description: `${PRODUCT} works support tickets end to end: diagnoses, researches, asks permission, fixes, and verifies the outcome independently.`,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return <html lang="en"><body><Providers>{children}</Providers></body></html>;
}
