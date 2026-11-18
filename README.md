# Timecard Exception Resolution Agent
> AI Innovation Challenge · Dayforce India · built by a team of six in 48 hours

An AI helper that finds mistakes on employee timecards, suggests a fix with the company policy behind it, checks that fix with code, and waits for a manager to approve. The AI never changes anyone's pay on its own.

## The problem
Managers still hunt for timecard mistakes by hand: missed punches, overtime, double-booked shifts, policy breaches. It's slow, and people get paid wrong.

## What it does
1. **Detects** problems with plain code (not AI): missed punch, overtime risk, schedule conflict, policy breach.
2. **Suggests** a fix with an open-weight LLM that cites the exact policy clause it used (retrieval over approved policy text).
3. **Validates** the suggestion with deterministic rules before anyone sees it.
4. **Asks a human.** A manager approves or rejects. Only an approval changes a timecard.
5. **Logs everything** in an audit trail.

If the AI is down, wrong or unsure, the system falls back to the rule-based fix or hands the case to a human.

## Why it's built this way
Value comes from combining LLM reasoning with trusted context, deterministic controls, validation and human accountability, not from using an LLM alone.

## Architecture
_Diagram: TODO_
`Timecards → Rule detectors → Policy retrieval → LLM proposal → Validator → Manager review → Audit log`

## Tech stack
Python 3.11 + FastAPI + Pydantic · PostgreSQL + pgvector · React + TypeScript (Vite) · open-weight LLM via an OpenAI-compatible API · Docker · Kubernetes (Kustomize) · GitHub Actions CI/CD

## Team
| Person | Role |
|---|---|
| Harsh | Lead + AI/RAG |
| Raman | Backend + DB |
| Ankit | Rules + Validator |
| Priyanshu | Frontend |
| Satyam | DevOps |
| Pranav | QA + Evaluation + Delivery |

## Repo workflow
Feature branches → pull request into `main` (CI must pass) → release merge into `production` (deploys to Kubernetes). Full project context is in [CLAUDE.md](CLAUDE.md).

## Run it
_Setup and run instructions: TODO (`make up` for local, `make k8s-up` for Kubernetes)._

## Safety, testing and evaluation
_Unit, integration and evaluation results with real numbers: TODO_

## Model licence note
_Model name, licence and acceptable-use check: TODO_

## Demo
_10-minute demo video link: TODO_

## Limitations and next steps
_TODO_
