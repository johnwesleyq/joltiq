"use client";
import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useEffect } from "react";
import { api, type Me } from "./api";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me"), retry: false, staleTime: 60_000 });
}

export function useApi<T>(key: QueryKey, path: string | null, opts: { refetchInterval?: number } = {}) {
  return useQuery({ queryKey: key, queryFn: () => api<T>(path!), enabled: !!path, refetchInterval: opts.refetchInterval });
}

/** Mutation that invalidates the given keys on success. */
export function useAct<TIn = unknown, TOut = unknown>(fn: (v: TIn) => Promise<TOut>, invalidate: QueryKey[] = []) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => invalidate.forEach((k) => qc.invalidateQueries({ queryKey: k })) });
}

/** Live updates over SSE: invalidates queries whenever the server announces relevant events. */
export function useLiveEvents(onEvent?: (kind: string, data: unknown) => void) {
  const qc = useQueryClient();
  useEffect(() => {
    const es = new EventSource("/api/v1/events/stream");
    const kinds = ["ticket.event", "agent.state", "agent.message", "approval.requested", "approval.decided", "session.event",
                   "verification.completed", "notification", "device.command_result"];
    const handler = (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      onEvent?.(e.type, data);
      if (e.type === "notification") qc.invalidateQueries({ queryKey: ["notifications"] });
      else qc.invalidateQueries({ predicate: (q) => ["ticket", "tickets", "run", "approvals", "session", "sessions"].includes(String(q.queryKey[0])) });
    };
    kinds.forEach((k) => es.addEventListener(k, handler));
    return () => es.close();
  }, [qc, onEvent]);
}
