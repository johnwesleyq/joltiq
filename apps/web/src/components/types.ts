export type Ticket = {
  id: string; number: number; subject: string; description: string; status: string; priority: string; category: string | null;
  source: string; requester: { name: string; email: string } | null; assignee: { id: string; name: string } | null; assignee_id: string | null;
  resolution_due: string | null; resolution_verified: boolean; created_at: string; updated_at: string; device_id: string | null; external_ref: string | null;
  ai_summary: string | null;
};
export type TicketDetail = Ticket & {
  comments: { id: string; body: string; internal: boolean; author_kind: string; author: { name: string } | null; created_at: string }[];
  timeline: { id: string; kind: string; actor_kind: string; data: Record<string, unknown>; at: string }[];
  attachments: { id: string; filename: string; size_bytes: number }[];
  allowed_transitions: string[];
};
export type Evidence = { id: string; kind: "internal" | "external"; citation: string; url?: string; text?: string; source_kind?: string;
  outcome?: string; flags?: string[]; retrieved_at?: string; live?: boolean; procedure?: boolean };
export type Step = { id: string; ordinal: number; action: string; params: Record<string, unknown>; risk: string; rationale: string;
  postconditions: { type: string; description?: string; params?: Record<string, unknown> }[]; status: string; target: Record<string, string | null> };
export type Approval = { id: string; status: string; action: string; risk: string; params: Record<string, unknown>; reason: string; effects: string;
  approver_roles: string[]; expires_at: string; decided_at: string | null; ticket_id?: string | null; run_id?: string | null; can_decide?: boolean };
export type Run = {
  id: string; ticket_id: string; state: string; paused_for: string | null; retries: number; max_retries: number; cost_usd: number;
  outcome_summary: string | null; messages: { id: string; role: string; content: string; data: Record<string, unknown>; at: string }[];
  transitions: { from: string; to: string; reason: string; at: string }[];
  plans: { id: string; revision: number; summary: string; hypothesis: string; risks: string[]; status: string; steps: Step[]; citations: Evidence[] }[];
  evidence: Evidence[]; hypotheses: { statement: string; confidence: number; evidence_ids: string[]; grounded: boolean }[];
  observations: string[]; approvals: Approval[];
  verifications: { id: string; status: string; details: { type: string; status: string; observed?: unknown; reason?: string; description?: string }[]; at: string }[];
  model_calls: { task: string; provider: string; model: string; live: boolean; ok: boolean; latency_ms: number; tokens: number; cost_usd: number }[];
  uses_test_fixture_model: boolean; article_id: string | null; research_query_id: string | null;
};
export type Session = {
  id: string; kind: string; state: string; ticket_id: string | null; device_id: string | null; allowed_domains: string[];
  consent: Record<string, unknown> | null; expires_at: string | null; emergency_stopped: boolean; pause_reason: string | null; run_id: string | null;
  control: { holder_kind: string; holder_user_id: string | null; expires_at: string } | null; created_at: string;
  can_consent?: boolean; you_hold_control?: boolean;
  browser?: { url?: string; title?: string; auth_required?: boolean; auth_kind?: string; blocked_requests?: string[]; error?: string };
};
