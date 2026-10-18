// Typed fetch wrapper for the JoltIQ API (same-origin via Next rewrite). Sends the CSRF token on unsafe methods
// and surfaces the API's error envelope; never invents success.
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown, public requestId?: string) {
    super(message);
  }
}

function csrf(): string | undefined {
  if (typeof document === "undefined") return undefined;
  return document.cookie.split("; ").find((c) => c.startsWith("jq_csrf="))?.split("=")[1];
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method || (init.json !== undefined ? "POST" : "GET")).toUpperCase();
  if (init.json !== undefined) headers.set("content-type", "application/json");
  if (method !== "GET") { const t = csrf(); if (t) headers.set("x-csrf-token", t); }
  const res = await fetch(`/api/v1${path}`, { ...init, method, headers, credentials: "same-origin",
    body: init.json !== undefined ? JSON.stringify(init.json) : init.body });
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  const body = ct.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const e = (body as { error?: { code: string; message: string; details?: unknown; request_id?: string } })?.error;
    throw new ApiError(res.status, e?.code || "http_error", e?.message || `Request failed (${res.status})`, e?.details, e?.request_id);
  }
  return body as T;
}

export type Page<T> = { items: T[]; total: number; page: number; page_size: number; has_more: boolean };

export type Me = {
  user: { id: string; email: string; name: string; email_verified: boolean; mfa_enabled: boolean; is_platform_owner: boolean };
  organizations: { id: string; name: string; role: string; branding: Record<string, string> }[];
  active_organization: { id: string; name: string; role: string; branding: Record<string, string> } | null;
  csrf_token: string;
};

export const STAFF = ["org_owner", "admin", "technician"];
export const ADMINS = ["org_owner", "admin"];

export function fmtDate(s?: string | null) {
  if (!s) return "—";
  return new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
