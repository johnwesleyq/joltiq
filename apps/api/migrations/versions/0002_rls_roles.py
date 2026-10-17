"""Row-level security, application role, audit immutability, full-text triggers.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "action_executions", "agent_messages", "agent_plans", "agent_runs", "agent_transitions", "ai_budgets",
    "approvals", "article_reviews", "audit_events", "contacts", "control_leases", "credentials", "customers",
    "deliveries", "device_commands", "device_enrollments", "device_nonces", "devices", "integrations",
    "invitations", "knowledge_articles", "knowledge_chunks", "knowledge_document_versions", "knowledge_documents",
    "model_calls", "model_provider_configs", "notification_preferences", "notifications", "plan_steps",
    "policy_rules", "research_findings", "research_queries", "research_sources", "session_events",
    "session_participants", "sla_policies", "subscriptions", "support_sessions", "sync_jobs",
    "ticket_attachments", "ticket_comments", "ticket_counters", "ticket_events", "tickets", "tool_calls",
    "usage_events", "verification_evidence", "verification_runs", "webhook_endpoints",
]

CTX = "nullif(current_setting('app.org_id', true), '')::uuid"
PLATFORM = "coalesce(current_setting('app.platform_admin', true), 'off') = 'on'"


def upgrade() -> None:
    # Application role: login, no superuser, cannot bypass RLS. Password is set by ops (see docs/operations).
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'joltiq_app') THEN
            CREATE ROLE joltiq_app LOGIN PASSWORD 'joltiq_app' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
          END IF;
        END $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO joltiq_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO joltiq_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO joltiq_app")
    # Audit log is append-only for the app.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM joltiq_app")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON alembic_version FROM joltiq_app")

    for t in TENANT_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} USING (org_id = {CTX} OR {PLATFORM}) "
            f"WITH CHECK (org_id = {CTX} OR {PLATFORM})"
        )
    # System-level audit rows (no org) may be written by unscoped contexts (e.g. failed login).
    op.execute(
        f"CREATE POLICY audit_system_insert ON audit_events FOR INSERT WITH CHECK (org_id IS NULL)"
    )

    # Full-text search vectors maintained by triggers (keyword half of hybrid retrieval).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION joltiq_chunk_tsv() RETURNS trigger AS $$
        BEGIN NEW.tsv := to_tsvector('english', coalesce(NEW.section,'') || ' ' || NEW.text); RETURN NEW; END
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_chunk_tsv BEFORE INSERT OR UPDATE OF text, section ON knowledge_chunks
          FOR EACH ROW EXECUTE FUNCTION joltiq_chunk_tsv();

        CREATE OR REPLACE FUNCTION joltiq_ticket_tsv() RETURNS trigger AS $$
        BEGIN NEW.search_tsv := setweight(to_tsvector('english', coalesce(NEW.subject,'')), 'A')
                             || setweight(to_tsvector('english', coalesce(NEW.description,'')), 'B'); RETURN NEW; END
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_ticket_tsv BEFORE INSERT OR UPDATE OF subject, description ON tickets
          FOR EACH ROW EXECUTE FUNCTION joltiq_ticket_tsv();
        CREATE INDEX ix_tickets_search_tsv ON tickets USING gin (search_tsv);
        CREATE INDEX ix_chunks_embedding ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding; DROP INDEX IF EXISTS ix_tickets_search_tsv")
    op.execute("DROP TRIGGER IF EXISTS trg_ticket_tsv ON tickets; DROP TRIGGER IF EXISTS trg_chunk_tsv ON knowledge_chunks")
    op.execute("DROP FUNCTION IF EXISTS joltiq_ticket_tsv(); DROP FUNCTION IF EXISTS joltiq_chunk_tsv()")
    op.execute("DROP POLICY IF EXISTS audit_system_insert ON audit_events")
    for t in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
