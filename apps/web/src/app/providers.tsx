"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

export default function Providers({ children }: { children: ReactNode }) {
  const [qc] = useState(() => new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: (n, e) => n < 1 && (e as { status?: number }).status !== 401 } } }));
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}
