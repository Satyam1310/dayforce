# Testing

What is tested at which level, how to run each level, and the real numbers. Every number here was
measured by running the command shown. Things that exist only in an open pull request are marked
**(open PR)**, and things not built yet say so.

## The levels

| Level | What it proves | Where | State |
|---|---|---|---|
| **Unit** | Each module does what it says, with the mock model | `backend/tests/unit/` | On `main` |
| **Integration (database)** | Real pgvector retrieval: ingest the policy, search it with real embeddings, calibrate the score threshold | `backend/tests/unit/ai/test_pgvector_integration.py` (marked `integration`) | On `main`. Needs a database, so it is skipped without one |
| **Integration (API)** | The API against a real Postgres: seed, propose, approve, reject, audit | `backend/tests/integration/` | **Open PR #12** |
| **Golden set** | 40 timecard cases with expected results: detection, validator, the whole workflow under five model behaviours, prompt injection | `backend/tests/golden/` | **Open PR #15** |
| **Property tests** | The same promises on random timecards (never crashes, deterministic, 12 hour and 10 hour rules) | `backend/tests/unit/rules/test_properties.py` | **Open PR #15** |
| **Frontend** | Components, pages and the real API client (with `fetch` faked) | `frontend/src/**/*.test.ts(x)` | On `main` (25 tests). More in open PRs #12 to #14 |
| **Smoke (real model)** | The real model returns valid JSON and grounded citations | `backend/scripts/smoke_llm.py`, `docs/llm_smoke_report.md` | On `main`. One real run so far |
| **System** | The whole flow against a running API | not built yet | Planned |

## How to run each level

Backend (Python 3.11, from `backend/`, virtual environment active):

```bash
python -m ruff check .                         # lint
python -m pytest                               # all unit tests (database tests are skipped)
python -m pytest --cov=app --cov=scripts       # with coverage
```

The database tests need a pgvector database. Start a throwaway one and point `DATABASE_URL` at
it (the password is a throwaway value for local use only):

```bash
docker run -d --name pgv-test -e POSTGRES_USER=app -e POSTGRES_PASSWORD=app \
  -e POSTGRES_DB=timecards -p 54329:5432 pgvector/pgvector:pg16
DATABASE_URL=postgresql://app:app@localhost:54329/timecards python -m pytest
docker rm -f pgv-test
```

The first run downloads the embedding model (about 64 MB, measured) into a temporary folder.

Golden set, report and property tests **(open PR #15)**:

```bash
python -m pytest tests/golden                  # the golden set as tests
python -m scripts.evaluate                     # offline report, writes docs/evaluation_report.md
PROPERTY_EXPLORE=1 PROPERTY_EXAMPLES=2000 python -m pytest tests/unit/rules/test_properties.py
```

Smoke test and demo walk-through:

```bash
python -m scripts.smoke_llm --mock             # no key, no network
python -m scripts.demo_flow --help             # narrated run of one case
# real model, at most 5 requests a minute, key in backend/.env (never in git):
python -m scripts.smoke_llm --model openai/gpt-oss-20b --max-rpm 5
```

Frontend (Node 20, from `frontend/`):

```bash
npm ci && npm run lint && npm test && npm run build
npm run format:check                           # after open PR #14 (Prettier)
```

## Real numbers

**On `main` today** (measured on Python 3.11 and Node 20):

- Backend: **249 tests pass** with a pgvector container, including the 6 database tests. Without
  a database, 243 pass and 6 are skipped. Ruff is clean.
- By area: detectors 64, validator 51, AI module and scripts 124 (6 of them database tests),
  contracts 10.
- Coverage: **99% overall** (1,554 statements, 13 missed), **100%** on `domain/rules` and
  `validator`, above the 90% target.
- Frontend: **25 tests pass**, lint and build are clean.

**With open PR #15** (golden set, evaluation, property tests): **342 backend tests pass** with a
database, 99% coverage, ruff clean. The offline evaluation (mock model, no network) gives:

- **40 of 40 golden cases pass** every check.
- Detection precision and recall are **100%** on every exception type (38 expected exceptions, 0
  missed, 0 extra). **0 false positives** on the 9 clean timecards.
- **378 of 378** deliberately bad fixes are rejected by the validator.
- In all five mock model behaviours (`ok`, `invalid_json`, `timeout`, `bad_citation`,
  `low_confidence`) the final proposal matched the safe outcome the policy requires, and no
  unvalidated fix ever reached a manager. With a working model, 7.9% of runs used the rule-based
  fix and 7.9% were escalated; these are the three cases whose rule-based fix itself breaks the
  rules.
- A simulated model that obeys hostile notes got **0 of 5** attacks through, with 0 tag breakouts.
- The property tests also passed on 2,000 fresh random examples each (about 14,000 cases).
- The evaluation catches regressions: changing one rule by one character in a scratch copy
  (`>` to `>=` on the 8 hour, 6 hour and 12 hour rules) failed the golden set each time.

**Real model smoke run** (Groq, `openai/gpt-oss-20b` and `openai/gpt-oss-120b`, 9 proposals per
model, 5 requests a minute): **0 call errors, 0 rate-limit responses, 100% valid JSON, 100% valid
citations, no fallbacks or escalations.** Median time per proposal 1.1 s (20b) and 1.6 s (120b);
95th percentile 1.8 s and 4.2 s. One injection note was not followed by either model.
**Caveat:** this run happened before the real validator existed, so it used an always-pass
validator. The real model with the real validator has not been run yet. The real-model evaluation
(`python -m scripts.evaluate --real`, open PR #15) has not been run either.

**Retrieval calibration** (pgvector, real embeddings; 8 on-topic and 8 off-topic queries): on-topic
queries scored 0.629 to 0.878, off-topic ones 0.406 to 0.511. The threshold is **0.57**, the
middle of that gap. Sixteen queries is a small sample, so re-run the calibration test if the
model or the policy text changes. The golden-set work also found that the query must be specific to
the rule: with one query per exception type, daily overtime scored 0.33 for its own clause (below
the 0.35 minimum). With a query per rule, every rule retrieves its own clause first at 0.80 to
0.95 with real embeddings.

## What is not tested

- No test of the whole flow against a running API with a browser. The system test is not built.
- The golden set is 40 cases we wrote. 100% on it shows the rules do what we meant, not that we
  meant everything right.
- No load or scaling test, and no Docker, Kubernetes or CI test: those do not exist yet.
- No test with real payroll data, and no test with a model other than the two in the smoke run.
