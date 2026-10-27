"""Typed schemas for model I/O. Every model output is validated against these before use; invalid output is
treated as a failed model call (never partially trusted)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Intent(BaseModel):
    category: Literal["incident", "how_to", "access_request", "billing", "account", "hardware", "other"]
    product: str | None = None
    urgency: Literal["low", "normal", "high", "urgent"] = "normal"
    summary: str = Field(max_length=500)
    needs_device: bool = False
    needs_browser: bool = False


class Clarification(BaseModel):
    sufficient: bool = Field(description="True if enough information exists to search and diagnose")
    questions: list[str] = Field(default_factory=list, max_length=5)
    known_facts: dict[str, str] = Field(default_factory=dict)


class Hypothesis(BaseModel):
    statement: str
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list, description="IDs of evidence items (E1, E2, ...) supporting this")
    contradicting_ids: list[str] = Field(default_factory=list)


class Diagnosis(BaseModel):
    hypotheses: list[Hypothesis] = Field(max_length=6)
    needs_external_research: bool = False
    research_query: str | None = None
    missing_information: list[str] = Field(default_factory=list)


class CandidateSelection(BaseModel):
    article_id: str | None = None
    hypothesis: str = ""


class Postcondition(BaseModel):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class ProposedStep(BaseModel):
    action: str = Field(description="Action id from the provided action registry ONLY")
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    postconditions: list[Postcondition] = Field(default_factory=list)


class ProposedPlan(BaseModel):
    summary: str
    hypothesis: str
    evidence_ids: list[str] = Field(default_factory=list)
    steps: list[ProposedStep] = Field(default_factory=list, max_length=12)
    risks: list[str] = Field(default_factory=list)
    escalate_instead: bool = False
    escalation_reason: str | None = None


class VerificationAnalysis(BaseModel):
    """Only used to *explain* verifier results to humans; the status itself is computed by code."""

    explanation: str
    customer_message: str


class ArticleDraft(BaseModel):
    title: str
    symptoms: list[str]
    prerequisites: list[str]
    diagnostics: list[str]
    remediation: list[str]
    risk: str
    verification: list[str]
    environment: list[str] = Field(default_factory=list)


class TicketSummary(BaseModel):
    summary: str
    category: str | None = None
    suggested_priority: Literal["low", "normal", "high", "urgent"] | None = None


class ResearchSynthesis(BaseModel):
    evidence: list[dict[str, Any]] = Field(default_factory=list, description="[{statement, source_ids, applies_to_versions}]")
    hypotheses: list[dict[str, Any]] = Field(default_factory=list, description="[{statement, why_unverified}]")
    conflicts: list[str] = Field(default_factory=list)


class ChatReply(BaseModel):
    reply: str
    cited_evidence_ids: list[str] = Field(default_factory=list)


class GuideStep(BaseModel):
    instruction: str
    why: str = ""


class TroubleshootingGuide(BaseModel):
    """Step-by-step guidance the PERSON performs themselves. Never executed by JoltIQ."""
    answer: str
    steps: list[GuideStep] = Field(default_factory=list, max_length=10)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    needs_technician: bool = False
    technician_reason: str | None = None


class GuidanceFeedback(BaseModel):
    outcome: str = Field(description="One of: fixed, not_fixed, unclear")
    new_information: str = ""


def json_schema(model: type[BaseModel]) -> dict:
    s = model.model_json_schema()
    return _inline_refs(s)


def _inline_refs(schema: dict) -> dict:
    defs = schema.pop("$defs", {})

    def walk(x):
        if isinstance(x, dict):
            if "$ref" in x:
                return walk(dict(defs[x["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x

    return walk(schema)
