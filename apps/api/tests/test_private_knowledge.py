"""Private RAG acceptance: two-tenant retrieval/prompt isolation and answer feedback binding."""
from __future__ import annotations

import uuid

import pytest
from conftest import register

from joltiq.services.ai.providers import ModelResult

pytestmark = pytest.mark.mocked


def test_knowledge_citations_are_stable_per_document(monkeypatch):
    from contextlib import nullcontext

    from joltiq.services import ask_chat
    from joltiq.services.knowledge import service as knowledge_service

    monkeypatch.setattr(knowledge_service, "search", lambda *_args, **_kwargs: [
        {"document_id": "doc-printer", "citation": "Printer SOP · § Triage · v1", "text": "Check power and network."},
        {"document_id": "doc-printer", "citation": "Printer SOP · § Fixes · v1", "text": "Clear a stuck queue."},
    ])
    class DB:
        def begin_nested(self):
            return nullcontext()

    system, citations = ask_chat._knowledge(DB(), uuid.uuid4(), [{"role": "user", "content": "printer offline"}], "org_owner")
    assert citations == [{"id": "K1", "citation": "Printer SOP · § Triage · v1"}]
    assert system.count("\n[K1] ") == 1 and "\n[K2] " not in system
    assert "Check power and network" in system and "Clear a stuck queue" in system


def _upload_and_index(owner, title: str, marker: str) -> None:
    body = f"# Rare printer recovery\n\nWhen rare printer code QZ-441 appears, use tenant marker {marker}.\n".encode()
    response = owner.post(
        "/api/v1/knowledge/documents",
        files={"file": (f"{title}.md", body, "text/markdown")},
        data={"title": title},
    )
    assert response.status_code == 201, response.text
    from joltiq.services.jobs import drain

    drain(["knowledge.index"])


def _connect(owner, suffix: str) -> None:
    response = owner.patch(
        "/api/v1/admin/models/openai",
        json={
            "enabled": True,
            "approved_for_tenant_data": True,
            "api_key": f"sk-test-private-knowledge-{suffix}-0000000000",
            "max_retries": 0,
        },
    )
    assert response.status_code == 200, response.text


def test_two_tenants_never_retrieve_or_prompt_with_each_others_knowledge(app, owner, new_client, monkeypatch):
    from joltiq.services import ask_chat
    from joltiq.services.knowledge import service as knowledge_service
    from joltiq.services.knowledge.embeddings import Embedder

    other = register(new_client(), "Other private tenant")
    monkeypatch.setattr(knowledge_service, "embedder_for", lambda *_args, **_kwargs: Embedder())
    _upload_and_index(owner, "Alpha printer SOP", "ALPHA-ONLY-7F3C")
    _upload_and_index(other, "Beta printer SOP", "BETA-ONLY-9D2A")

    alpha_hits = owner.post("/api/v1/knowledge/search", json={"query": "rare printer code QZ-441"}).json()["results"]
    beta_hits = other.post("/api/v1/knowledge/search", json={"query": "rare printer code QZ-441"}).json()["results"]
    assert "ALPHA-ONLY-7F3C" in str(alpha_hits) and "BETA-ONLY-9D2A" not in str(alpha_hits)
    assert "BETA-ONLY-9D2A" in str(beta_hits) and "ALPHA-ONLY-7F3C" not in str(beta_hits)

    _connect(owner, "alpha")
    _connect(other, "beta")
    captured: list[dict] = []

    class Client:
        def complete(self, **kwargs):
            captured.append(kwargs)
            return ModelResult(
                text="Use the retrieved tenant procedure. [K1]",
                input_tokens=40,
                output_tokens=12,
                model="gpt-6-luna",
                provider="openai",
            )

    monkeypatch.setattr(ask_chat, "build", lambda *_args, **_kwargs: Client())
    alpha_answer = owner.post("/api/v1/agents/chat", json={"message": "What fixes rare printer code QZ-441?"})
    beta_answer = other.post("/api/v1/agents/chat", json={"message": "What fixes rare printer code QZ-441?"})
    assert alpha_answer.status_code == 201 and beta_answer.status_code == 201
    assert alpha_answer.json()["reply"]["sources"][0]["id"] == "K1"
    assert "Alpha printer SOP" in alpha_answer.json()["reply"]["sources"][0]["citation"]
    assert beta_answer.json()["reply"]["sources"][0]["id"] == "K1"
    alpha_run = owner.get(f"/api/v1/agents/runs/{alpha_answer.json()['run_id']}").json()
    alpha_message = next(message for message in alpha_run["messages"] if message["role"] == "assistant")
    assert alpha_message["data"]["citations"] == alpha_answer.json()["reply"]["sources"]

    alpha_prompt, beta_prompt = captured[-2]["system"], captured[-1]["system"]
    assert "[K1]" in alpha_prompt and "Alpha printer SOP" in alpha_prompt and "ALPHA-ONLY-7F3C" in alpha_prompt
    assert "BETA-ONLY-9D2A" not in alpha_prompt
    assert "[K1]" in beta_prompt and "Beta printer SOP" in beta_prompt and "BETA-ONLY-9D2A" in beta_prompt
    assert "ALPHA-ONLY-7F3C" not in beta_prompt


def test_answer_feedback_is_idempotent_assistant_only_and_tenant_bound(app, owner, new_client, monkeypatch):
    from joltiq.services import ask_chat
    from joltiq.services.knowledge import service as knowledge_service
    from joltiq.services.knowledge.embeddings import Embedder

    _connect(owner, "feedback")
    monkeypatch.setattr(knowledge_service, "embedder_for", lambda *_args, **_kwargs: Embedder())

    class Client:
        def complete(self, **_kwargs):
            return ModelResult(text="Try reconnecting the printer. [K1]", model="gpt-6-luna", provider="openai")

    monkeypatch.setattr(ask_chat, "build", lambda *_args, **_kwargs: Client())
    created = owner.post("/api/v1/agents/chat", json={"message": "Printer is offline"}).json()
    run = owner.get(f"/api/v1/agents/runs/{created['run_id']}").json()
    assistant = next(message for message in run["messages"] if message["role"] == "assistant")
    user_message = next(message for message in run["messages"] if message["role"] == "user")

    first = owner.put(
        f"/api/v1/agents/messages/{assistant['id']}/feedback",
        json={"helpful": True, "comment": "Solved it"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["helpful"] is True and first.json()["comment"] == "Solved it"

    corrected = owner.put(
        f"/api/v1/agents/messages/{assistant['id']}/feedback",
        json={"helpful": False, "comment": "Issue returned"},
    )
    assert corrected.status_code == 200
    assert corrected.json()["id"] == first.json()["id"] and corrected.json()["helpful"] is False
    refreshed = owner.get(f"/api/v1/agents/runs/{created['run_id']}").json()
    assert next(message for message in refreshed["messages"] if message["id"] == assistant["id"])["feedback"] is False

    rejected = owner.put(f"/api/v1/agents/messages/{user_message['id']}/feedback", json={"helpful": True})
    assert rejected.status_code == 400 and rejected.json()["error"]["code"] == "validation_error"

    other = register(new_client(), "Feedback isolation tenant")
    hidden = other.put(f"/api/v1/agents/messages/{assistant['id']}/feedback", json={"helpful": True})
    assert hidden.status_code == 404

    from sqlalchemy import func, select

    from joltiq.db import session_scope
    from joltiq.models import KnowledgeAnswerFeedback

    with session_scope(uuid.UUID(owner.org_id)) as db:
        assert db.execute(select(func.count()).select_from(KnowledgeAnswerFeedback)).scalar_one() == 1
