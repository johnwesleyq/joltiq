"""LOCAL TEST FIXTURE — deterministic rule engine standing in for an LLM when no provider credentials exist.

It is not artificial intelligence. It exists so the full workflow (intake → retrieval → plan → approval → execution
→ verification) can be exercised end-to-end in development/CI. Every ModelCall it produces is stored with
live=False and the UI labels it "Local test model". It is refused in production (see Settings.assert_production_safe).

Planning rule: it only ever proposes steps copied from technician-APPROVED executable knowledge procedures found in
the retrieved evidence. It never turns web research into executable steps; such cases are escalated.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .providers import ModelResult

MARK = "CONTEXT_JSON:"


def _ctx(messages: list[dict]) -> dict[str, Any]:
    for m in reversed(messages):
        c = m.get("content") or ""
        if MARK in c:
            try:
                obj, _ = json.JSONDecoder().raw_decode(c.split(MARK, 1)[1].strip())
                return obj
            except json.JSONDecodeError:
                return {}
    return {}


def _first_sentence(t: str, n: int = 220) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    m = re.match(r"(.{20," + str(n) + r"}?[.!?])\s", t + " ")
    return (m.group(1) if m else t[:n]).strip()


class LocalFixtureProvider:
    name = "local_fixture"
    live = False

    def complete(self, *, model: str, system: str, messages: list[dict], schema: dict | None = None,
                 schema_name: str = "result", max_tokens: int = 2000, images: list[str] | None = None) -> ModelResult:
        ctx = _ctx(messages)
        fn = getattr(self, f"_{schema_name}", None)
        data = fn(ctx, messages) if fn else {}
        text = json.dumps(data)
        return ModelResult(text, data, input_tokens=sum(len(m.get("content", "")) // 4 for m in messages),
                           output_tokens=len(text) // 4, model="local-rules-v1", provider=self.name, live=False, latency_ms=1)

    # ---- task handlers ----
    def _Intent(self, ctx, messages):  # noqa: N802
        text = (ctx.get("text") or messages[-1]["content"]).lower()
        cat = "incident"
        if any(w in text for w in ("how do i", "how to", "where can i")):
            cat = "how_to"
        elif any(w in text for w in ("password", "access", "permission", "locked out")):
            cat = "access_request"
        elif any(w in text for w in ("invoice", "billing", "charge")):
            cat = "billing"
        elif any(w in text for w in ("printer", "monitor", "keyboard", "laptop won't")):
            cat = "hardware"
        product = next((p for p in ("outlook", "excel", "teams", "quickbooks", "acrobat", "vpn", "chrome", "printer", "portal")
                        if p in text), None)
        return {"category": cat, "product": product, "urgency": "high" if "urgent" in text or "down" in text else "normal",
                "summary": _first_sentence(ctx.get("text") or messages[-1]["content"], 300),
                "needs_device": any(w in text for w in ("outlook", "excel", "windows", "crash", "my computer", "laptop", "pc")),
                "needs_browser": any(w in text for w in ("website", "portal", "web app", "browser", "online account"))}

    def _Clarification(self, ctx, messages):  # noqa: N802
        text = ctx.get("text", "")
        facts = {}
        if re.search(r"\b(version|build|20\d\d|365)\b", text, re.I):
            facts["version_mentioned"] = "yes"
        sufficient = len(text) >= 40
        qs = [] if sufficient else ["What exactly happens (error message, crash, freeze)?",
                                    "When did it start and what changed around then?",
                                    "Which application and version are you using?"]
        return {"sufficient": sufficient, "questions": qs, "known_facts": facts}

    def _Diagnosis(self, ctx, messages):  # noqa: N802
        ev = ctx.get("evidence", [])
        hyps = []
        for e in ev[:4]:
            if e.get("kind") == "internal":
                conf = 0.75 if e.get("outcome") == "verified" or e.get("procedure") else 0.5
                hyps.append({"statement": _first_sentence(e.get("text", "")), "confidence": conf, "evidence_ids": [e["id"]], "contradicting_ids": []})
            elif e.get("kind") == "external":
                hyps.append({"statement": _first_sentence(e.get("text", "")), "confidence": 0.35 if e.get("source_kind") == "official_vendor" else 0.2,
                             "evidence_ids": [e["id"]], "contradicting_ids": []})
        internal = [e for e in ev if e.get("kind") == "internal"]
        need_research = not internal and not ctx.get("research_done")
        return {"hypotheses": hyps, "needs_external_research": need_research,
                "research_query": ctx.get("subject") if need_research else None,
                "missing_information": [] if hyps else ["No matching internal or external evidence"]}

    def _ProposedPlan(self, ctx, messages):  # noqa: N802
        procs = ctx.get("procedures", [])
        failed = set(ctx.get("previously_failed_procedures", []))
        procs = [p for p in procs if p.get("article_id") not in failed]
        if procs:
            p = procs[0]
            return {"summary": f"Apply approved procedure: {p['title']}", "hypothesis": p.get("hypothesis", p["title"]),
                    "evidence_ids": [p["evidence_id"]], "steps": p["steps"], "risks": p.get("risks", []), "escalate_instead": False}
        has_external = any(e.get("kind") == "external" for e in ctx.get("evidence", []))
        reason = ("Only external, unverified fixes were found; a technician must review before anything runs on the customer's system."
                  if has_external else "No approved procedure matches this issue.")
        return {"summary": "Escalate to a technician", "hypothesis": (ctx.get("top_hypothesis") or ""), "evidence_ids": [],
                "steps": [], "risks": [], "escalate_instead": True, "escalation_reason": reason}

    def _VerificationAnalysis(self, ctx, messages):  # noqa: N802
        status = ctx.get("status", "UNKNOWN")
        msgs = {"VERIFIED": "The fix was applied and independently confirmed.",
                "FAILED": "The change was attempted but the check afterwards shows the problem is not fixed. A technician has been notified.",
                "PARTIAL": "Some checks passed and some did not. A technician will follow up.",
                "UNKNOWN": "We could not confirm the result, so we are not marking this as fixed. A technician will follow up."}
        return {"explanation": "; ".join(f"{d.get('type')}: {d.get('status')}" for d in ctx.get("details", [])) or status,
                "customer_message": msgs.get(status, msgs["UNKNOWN"])}

    def _ArticleDraft(self, ctx, messages):  # noqa: N802
        return {"title": f"Resolved: {ctx.get('subject', 'Support issue')}", "symptoms": [ctx.get("description", "")[:300]],
                "prerequisites": ["Customer consent for a remote session", "Enrolled device or approved browser domain"],
                "diagnostics": [o for o in ctx.get("observations", [])][:6] or ["Reproduce the reported symptom"],
                "remediation": [f"{s['action']} {json.dumps(s.get('params', {}))}" for s in ctx.get("steps", [])],
                "risk": ", ".join(sorted({s.get("risk", "") for s in ctx.get("steps", [])})) or "LOW_RISK",
                "verification": [d.get("description") or d.get("type") for d in ctx.get("verification", [])],
                "environment": ctx.get("environment", [])}

    def _TicketSummary(self, ctx, messages):  # noqa: N802
        return {"summary": _first_sentence(ctx.get("text", ""), 400), "category": None, "suggested_priority": None}

    def _ResearchSynthesis(self, ctx, messages):  # noqa: N802
        ev, hy = [], []
        for s in ctx.get("sources", []):
            item = {"statement": _first_sentence(s.get("excerpt", "")), "source_ids": [s["id"]], "applies_to_versions": s.get("versions", [])}
            if s.get("source_kind") == "official_vendor":
                ev.append(item)
            else:
                hy.append({"statement": item["statement"], "why_unverified": f"from {s.get('source_kind')} source; not vendor-confirmed"})
        return {"evidence": ev, "hypotheses": hy, "conflicts": []}

    def _ChatReply(self, ctx, messages):  # noqa: N802
        ev = ctx.get("evidence", [])
        state = ctx.get("state", "")
        if ev:
            top = ev[0]
            reply = f"I found related guidance ([{top['id']}] {top.get('citation', '')}). {_first_sentence(top.get('text', ''))}"
            return {"reply": reply, "cited_evidence_ids": [top["id"]]}
        return {"reply": f"Thanks — I'm working on this (status: {state.replace('_', ' ').lower() or 'investigating'}).", "cited_evidence_ids": []}
