-- Backup schema (CLAUDE.md section 4: plain SQL, created at startup, no ORM/Alembic).
-- Each table keeps the full contract object as JSONB in `doc` (validated by the Pydantic
-- models on every read/write) plus the columns we filter or join on.
-- Status columns are kept in sync with doc->>'status' by the repository functions.

CREATE TABLE IF NOT EXISTS timecards (
    id          TEXT PRIMARY KEY,
    employee_id TEXT NOT NULL,
    work_date   DATE NOT NULL,
    doc         JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS timecards_employee_date ON timecards (employee_id, work_date);

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

CREATE TABLE IF NOT EXISTS decisions (
    id          TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE REFERENCES proposals (id) ON DELETE CASCADE,
    doc         JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id        TEXT PRIMARY KEY,
    ts        TIMESTAMPTZ NOT NULL,
    entity_id TEXT NOT NULL,
    doc       JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_events_entity ON audit_events (entity_id, ts);
