# Backend API

FastAPI + PostgreSQL (pgvector image), plain SQL with psycopg 3, no ORM. Wired to the AI
workflow in `app/ai/workflow.py`. Interactive docs: http://localhost:8000/docs

## Run locally

```bash
# 1. Postgres
docker run -d --name dayforce-db -e POSTGRES_USER=app -e POSTGRES_PASSWORD=app \
  -e POSTGRES_DB=timecards -p 5432:5432 pgvector/pgvector:pg16

# 2. Settings: env vars only. For local dev put them in backend/.env (gitignored):
#    DATABASE_URL=postgresql://app:app@localhost:5432/timecards
#    LLM_PROVIDER=mock            # or openai_compatible + LLM_BASE_URL / LLM_MODEL / LLM_API_KEY

# 3. API (creates the tables on startup)
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 4. Starting data + policy clauses
python -m app.db.seed            # 8 demo cases (case 1 = the contract fixtures), with history
python -m app.db.seed --open     # same cases, all OPEN: run the AI live
python -m app.ai.ingest          # optional: embed the policy into policy_chunks (RETRIEVER=pgvector)
```

## Endpoints

| Method + path | What it does | Errors |
|---|---|---|
| `GET /health` | DB reachable? `{"status":"ok","database":"ok"}` | 503 when the DB is down |
| `GET /metrics` | Prometheus metrics | |
| `GET /api/cases?status=&type=` | `CaseListItem[]` = `{ exception, proposal }` | 422 bad filter |
| `GET /api/cases/{id}` | `CaseDetail` = `{ timecard, exception, proposal, decision, audit_events }` | 404 |
| `POST /api/cases/{id}/propose` | Runs the AI workflow, saves the `Proposal` and the audit events it returns | 404, 409 `CASE_RESOLVED`, 409 `PROPOSAL_PENDING` |
| `POST /api/cases/{id}/approve` | Body `{ manager_id, comment? }`. Applies the fix, resolves the case, returns `CaseDetail` | 404, 409 `CASE_ESCALATED`, 409 `NO_PROPOSAL`, 409 `ALREADY_DECIDED`, 422 |
| `POST /api/cases/{id}/reject` | Body `{ manager_id, comment }` (comment required). Nothing changes on the timecard; case back to `OPEN` | same as approve, 422 `COMMENT_REQUIRED` |
| `GET /api/audit?entity_id=` | `AuditEvent[]`, newest first | |
| `POST /api/dev/seed?with_history=` | Demo only: reset and load the starting data | |

A case id is the exception id. All objects are the models in `app/contracts/models.py`,
unchanged. Every error: `{ "error": { "code", "message", "request_id" } }`.

## How it behaves (Harsh's checklist)

- **One transaction per request** (`app/db/connection.get_conn`): a decision, the timecard
  change and the audit events are saved together or not at all.
- **Approving twice does not apply twice**: the case row is locked (`SELECT ... FOR UPDATE`),
  only a `PENDING_REVIEW` proposal can be decided (else 409), and `decisions.proposal_id` is
  `UNIQUE`.
- **Escalated cases cannot be approved**: 409 `CASE_ESCALATED`.
- **Audit events are append-only**: a database trigger rejects every `UPDATE` / `DELETE`
  on `audit_events` (tested). Only the demo seed resets the whole table (`TRUNCATE`).
- **Settings from env vars only**: no password or key in the code. Without `DATABASE_URL`
  the API answers 503 `DB_NOT_CONFIGURED`.
- **Stateless**: nothing kept in memory between requests, so it can run as many copies.
  Schema setup at startup takes an advisory lock, so copies starting together do not race.
- **Parameterised SQL only.**
- **Logs**: JSON lines with a request id; no request bodies, notes, DSNs or keys are logged.
- **Only a manager's approval changes a timecard.** Propose never does.

## Tests

```bash
cd backend
python -m pytest tests/unit -q                       # no database needed
export DATABASE_URL=postgresql://app:app@localhost:5432/timecards
python -m pytest tests/integration/test_api_flow.py  # every endpoint; RESETS the app tables
```

## Simplifications / owned elsewhere

| Area | Here | Owner |
|---|---|---|
| Rule detectors | Not called by the API yet (`app/domain/rules`) | Ankit |
| Validator | Uses `app.validator.validate_proposal` if present, else pass-through | Ankit |
| `DEDUCT_BREAK` on approve | Adds a BREAK_START/BREAK_END pair centred in the worked span (the contract has no break field) | team decision |
| `REASSIGN_SHIFT` on approve | Removes that shift from this employee's timecard | team decision |
| Docker / compose / Kubernetes | Not here | Satyam |
