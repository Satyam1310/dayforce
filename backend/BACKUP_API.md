# Backup API (not the team's official backend)

Built by Priyanshu as a **fallback** in case the official API (Raman's card) is not ready in
time. It lives on a local branch and is only offered to the team if they agree.
It follows CLAUDE.md sections 4, 8, 9 and 10 and plugs into Harsh's `app.ai.workflow`.

## Run locally

```bash
# 1. Postgres (same image as the team plan)
docker run -d --name dayforce-db -e POSTGRES_USER=app -e POSTGRES_PASSWORD=app \
  -e POSTGRES_DB=timecards -p 5432:5432 pgvector/pgvector:pg16

# 2. API (mock LLM by default; set LLM_* in backend/.env to use Groq)
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
# docs: http://localhost:8000/docs

# 3. Demo data
curl -X POST "http://localhost:8000/api/v1/seed/load?with_history=false"   # all cases OPEN
curl -X POST "http://localhost:8000/api/v1/seed/load"                      # with history

# 4. Frontend against it
cd ../frontend && echo "VITE_USE_MOCK=false" > .env.local && npm run dev
```

## Tests

```bash
cd backend
pytest tests/unit/api                                   # no database needed
export DATABASE_URL=postgresql://app:app@localhost:5432/timecards
pytest -m integration tests/integration/test_api_flow.py   # WIPES the app tables
```

## What is simplified (and who owns the real thing)

| Area | Backup does | Real owner |
|---|---|---|
| Tables | One JSONB `doc` per contract object + filter columns | Raman (normalised tables) |
| Seed | The 8 frontend demo cases (`app/db/seed_data.json`) | Ankit (`data/seed`, >= 60 timecards) |
| `POST /analysis/run` | Calls `app.domain.rules.detect_exceptions` if it exists, else 501 | Ankit |
| Validator | `app.validator.validate_proposal` if it exists, else always-pass | Ankit |
| `DEDUCT_BREAK` approval | Adds a BREAK_START/BREAK_END pair centred in the worked span (contract has no break field) | Team decision |
| `REASSIGN_SHIFT` approval | Removes the shift from this employee's timecard | Team decision |
| Docker / compose / k8s | Not included | Satyam |
| `docs/openapi.yaml` | Not exported (see `/openapi.json` or `/docs`) | Raman / Satyam |
