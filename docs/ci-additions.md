# What CI should add (for Satyam)

Harsh did not edit `.github/workflows/`. This is the list of what the current `ci.yml` is
missing, with the exact commands. Today CI runs `ruff check .` and `pytest -q` without a
database, so every test that needs Postgres is **skipped** (the integration tests) or starts
its own Docker container (the system tests). Run times below were measured on a laptop
(Apple silicon, warm caches); a hosted runner will be slower, plan for 2x.

## 1. A pgvector service container, so the database tests really run

Add to the `backend` job, and set `DATABASE_URL` on the test step:

```yaml
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_USER: app
          POSTGRES_PASSWORD: app
          POSTGRES_DB: timecards
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U app -d timecards"
          --health-interval 5s --health-timeout 5s --health-retries 10
```

```yaml
      - name: Run backend tests (with the database)
        working-directory: backend
        env:
          DATABASE_URL: postgresql://app:app@localhost:5432/timecards
        run: python -m pytest -q
```

What this switches on: `tests/integration/` (29 tests, every endpoint against real Postgres),
`tests/unit/ai/test_pgvector_integration.py` (the pgvector retriever), and the system tests.
Without `DATABASE_URL` the integration tests skip silently, so a green CI today does **not**
mean they passed.

The pgvector tests download the embedding model `BAAI/bge-small-en-v1.5` (about 64 MB) on first
use into the system temp directory. Cache it between runs, for example with
`actions/cache` on the folder `${{ runner.temp }}/fastembed_cache`, and set
`FASTEMBED_CACHE_PATH` to it. Without a cache the first run is slower (download time not measured).

Expected time: whole backend suite with the database about 10 seconds on a laptop
(`python -m pytest -q`: 381 tests before this work, more now; see the PR for the current count).

## 2. The system test

`backend/tests/system/` starts the real API as a separate process (uvicorn) and talks to it over
HTTP. It needs no extra step: it runs as part of `python -m pytest -q`, and uses `DATABASE_URL`
when it is set (the service container above). If it is not set, the tests start a
`pgvector/pgvector:pg16` container themselves, which also works on `ubuntu-latest`. It is
**not skipped** when no database is found: it fails, on purpose.

To run only the system tests (as their own visible step):

```yaml
      - name: System tests
        working-directory: backend
        env:
          DATABASE_URL: postgresql://app:app@localhost:5432/timecards
        run: python -m pytest tests/system -q
```

Expected time: about 9 seconds including starting a container (19 tests; it starts 6 API
processes, one per LLM failure mode).

## 3. Prettier check (frontend)

The frontend job runs lint, tests and build, but not the formatting check that PR #14 added:

```yaml
      - name: Check formatting
        working-directory: frontend
        run: npm run format:check
```

Expected time: about 2 seconds after `npm ci`.

## 4. OpenAPI drift check

`docs/openapi.yaml` must match the app. A unit test already checks this
(`tests/unit/api/test_openapi.py`), so it runs in the normal pytest step. For a clearer failure
message you can also add a step:

```yaml
      - name: openapi.yaml is up to date
        working-directory: backend
        run: |
          python -m scripts.export_openapi
          git diff --exit-code ../docs/openapi.yaml
```

Expected time: under 1 second.

## 5. The evaluation (mock model, no network)

```yaml
      - name: Evaluation on the golden set (mock LLM)
        working-directory: backend
        run: |
          python -m scripts.evaluate
          git diff --exit-code ../docs/evaluation_report.md
```

`scripts.evaluate` exits with a non-zero code if any golden case fails, and the report is
deterministic, so the `git diff` also catches a report that was not regenerated.
Expected time: under 1 second. Never run it with `--real` in CI (that calls the real model).

## 6. Environment for CI

Nothing needs a secret. Do not set `LLM_API_KEY` in CI. The tests set `LLM_PROVIDER=mock`
themselves. `ENABLE_DEV_SEED` should stay unset in any deployed environment (the endpoint
answers 404 when it is off).

## 7. Later (not needed now)

- `scripts/load_test.py` against the docker-compose stack with `LLM_PROVIDER=mock`, as a manual
  or nightly job, never on Groq. 200 flows (1,200 requests) took about 5 seconds locally.
