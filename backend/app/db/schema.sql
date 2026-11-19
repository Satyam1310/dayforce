-- Database schema. Plain SQL, run at app startup (idempotent). No ORM, no migrations tool.
-- Each table keeps the full contract object (backend/app/contracts/models.py) as JSONB in
-- `doc`, validated by Pydantic on every read and write, plus the columns we filter or join on.
-- Status columns are kept in sync with doc->>'status' by app/db/repo.py.

-- Policy clauses for RAG. Same DDL as POLICY_CHUNKS_DDL in app/ai/ingest.py: keep identical.
-- 384 = embedding size of BAAI/bge-small-en-v1.5. Filled by: python -m app.ai.ingest
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS policy_chunks (
  clause_id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL,
  embedding vector(384) NOT NULL);

-- One employee, one day.
CREATE TABLE IF NOT EXISTS timecards (
    id          TEXT PRIMARY KEY,
    employee_id TEXT NOT NULL,
    work_date   DATE NOT NULL,
    doc         JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS timecards_employee_date ON timecards (employee_id, work_date);

-- Cases = detected exceptions (DetectedException). The case id is the exception id.
CREATE TABLE IF NOT EXISTS exceptions (
    id          TEXT PRIMARY KEY,
    timecard_id TEXT NOT NULL REFERENCES timecards (id) ON DELETE CASCADE,
    type        TEXT NOT NULL,
    status      TEXT NOT NULL,
    work_date   DATE NOT NULL,
    doc         JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS exceptions_status_type ON exceptions (status, type);

CREATE TABLE IF NOT EXISTS proposals (
    id           TEXT PRIMARY KEY,
    exception_id TEXT NOT NULL REFERENCES exceptions (id) ON DELETE CASCADE,
    status       TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    doc          JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS proposals_exception ON proposals (exception_id, created_at);

-- A manager decision. UNIQUE(proposal_id): a proposal can be decided only once.
CREATE TABLE IF NOT EXISTS decisions (
    id          TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE REFERENCES proposals (id) ON DELETE CASCADE,
    doc         JSONB NOT NULL
);

-- Audit trail. Append-only: the trigger below rejects any UPDATE or DELETE.
CREATE TABLE IF NOT EXISTS audit_events (
    id        TEXT PRIMARY KEY,
    ts        TIMESTAMPTZ NOT NULL,
    entity_id TEXT NOT NULL,
    doc       JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_events_entity ON audit_events (entity_id, ts);

CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'audit_events is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER audit_events_no_change
  BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
