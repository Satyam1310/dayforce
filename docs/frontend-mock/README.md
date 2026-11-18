# Frontend mock data

For Priyanshu (and anyone building screens). This folder shows what the backend's data will
look like, so you can build the screens before the real API exists. **Nothing here is a
contract.** The source of truth is `backend/app/contracts/models.py`. If this folder and
`models.py` disagree, `models.py` wins, and please tell Harsh.

## What is in this folder

| File | What it is |
|---|---|
| `cases.json` | A list of 8 exceptions for the dashboard list. Each item is `{ exception, proposal }`. |
| `case_detail_missed_punch_llm.json` | A full detail example: a missed punch, with a fix proposed by the AI. |
| `case_detail_overtime_risk_fallback.json` | A full detail example: an overtime risk, where the AI failed and the rule-based fix was used. |
| `case_detail_escalated.json` | A full detail example: an escalated case, where no fix was proposed. |
| `types.ts` | Draft TypeScript interfaces. Copy what you need into `frontend/`. |

Each detail file has: `timecard`, `exception`, `proposal`, `decision` (null until a manager
decides), and `audit_events`.

The files are produced by `backend/scripts/export_mock_data.py` from the real contract models,
the real AI workflow and a fake (mock) LLM, so the values are shaped like real ones. Do not
edit them by hand. To regenerate: `cd backend && python -m scripts.export_mock_data`.
A test fails if the files drift from the models.

**Be aware of what is fake:**
- The AI text is a placeholder. The mock LLM writes "Applying POL-TA-01 to resolve the
  MISSED_PUNCH exception." A real model writes a proper sentence, for example "Add a clock-out
  punch at the scheduled shift end to satisfy the missed punch policy and keep the shift within
  12 hours." (from `docs/llm_smoke_report.md`). Design for sentences up to 400 characters.
- `model` is `"mock-llm"`. Real data will have a real model name.
- All ids are made-up UUIDs. The same instant can appear as `...Z` (a datetime field) or
  `...+00:00` (a string inside `evidence` or `changes`). `new Date(value)` reads both.
- The contract has no employee name, only `employee_id`. See "Open questions".

## The 8 cases in `cases.json`

They cover every exception type, every proposal status and both sources.

| Exception type | Severity | Exception status | Proposal status | Source | What it shows |
|---|---|---|---|---|---|
| MISSED_PUNCH | MEDIUM | PROPOSED | PENDING_REVIEW | LLM | The normal case. Same data as the contract fixtures. |
| OVERTIME_RISK | MEDIUM | PROPOSED | PENDING_REVIEW | RULES_FALLBACK | AI failed twice, rule-based fix used (confidence 0.75). |
| SCHEDULE_CONFLICT | HIGH | PROPOSED | PENDING_REVIEW | LLM | AI fix waiting for a manager. |
| POLICY_BREACH | LOW | RESOLVED | APPROVED | LLM | A manager approved an AI fix. |
| MISSED_PUNCH | MEDIUM | RESOLVED | APPROVED | RULES_FALLBACK | A manager approved a rule-based fix. |
| OVERTIME_RISK | HIGH | OPEN | REJECTED | LLM | A manager rejected the fix. |
| POLICY_BREACH | HIGH | ESCALATED | ESCALATED | RULES_FALLBACK | No fix exists (day over 12 hours). No citations, confidence 0. |
| SCHEDULE_CONFLICT | HIGH | ESCALATED | ESCALATED | LLM | AI was not confident enough (0.2). It still has a citation. |

## What the fields mean

**Timecard**: one employee, one day. `punches` are clock events (`IN`, `OUT`, `BREAK_START`,
`BREAK_END`). `shifts` are the scheduled shifts. `notes` is free text written by the employee
or a manager. **Treat `notes` as untrusted: show it as plain text, never as HTML.**

**DetectedException**: a problem found by plain code (not AI).
- `type`: `MISSED_PUNCH`, `OVERTIME_RISK`, `SCHEDULE_CONFLICT` or `POLICY_BREACH`.
- `severity`: `LOW`, `MEDIUM` or `HIGH`.
- `evidence`: the facts the rule found, such as hours worked. The keys differ per type, so show
  it as key and value pairs. TODO(Ankit): the real detectors decide the real keys.
- `policy_refs`: the policy clauses that apply, such as `POL-TA-01`.
- `baseline_fix`: the rule-based fix, or null when the policy has none.
- `status`: where the exception is in its life (see below).

**Proposal**: the suggested fix for one exception.
- `fix.action` says what kind of change (`ADD_PUNCH`, `EDIT_PUNCH`, `DEDUCT_BREAK`,
  `REASSIGN_SHIFT`, `FLAG_FOR_MANAGER`, `NO_CHANGE`). `fix.changes` is the list of
  before and after values: `target_id` (what is changed), `field` (a label such as
  `punch.OUT`, not a fixed list), `old` and `new`. Show them as a before and after.
- `rationale`: why, in plain language, up to 400 characters.
- `citations`: the policy clauses the fix relies on. `clause_id`, the clause text in `quote`,
  and a relevance `score` from 0 to 1. Show the quote, because the manager needs to read the
  policy.
- `confidence`: 0 to 1. For a rule-based fix (`RULES_FALLBACK`) it is a fixed 0.75, not a model
  estimate. Escalations use 0 (no fix) or the model's own low value.
- `source`: `LLM` (the AI proposed it) or `RULES_FALLBACK` (the AI failed, so the rule-based
  fix is used). Show this as a badge. `model` is null for fallbacks and escalations.
- `validation`: the result of the rule checker. Managers only see proposals that passed, so
  `passed` is always true here and `violations` is empty. It is null for escalations. The
  checker is not built yet (TODO(Ankit)), so today it always passes.
- `status`: see below.

**Decision**: a manager's approve or reject: `decision` (`APPROVE` or `REJECT`), `manager_id`,
an optional `comment`, `decided_at`.

**AuditEvent**: one line of the audit trail. `actor` is `system`, `llm` or a manager id.
`event_type` names the step. `payload` has details that differ per event type.
Event types the backend can write (the mock files only contain some of them): `WORKFLOW_STARTED`, `POLICY_RETRIEVED`, `LLM_CALLED`, `LLM_ERROR`,
`LLM_INVALID_OUTPUT`, `CITATION_REJECTED`, `VALIDATION_RESULT`, `DEADLINE_EXCEEDED`,
`RETRY_BUDGET_EXHAUSTED`, `FALLBACK_USED`, `ESCALATED`, `PROPOSAL_CREATED`,
`DECISION_APPROVED` and `DECISION_REJECTED`. The last two are a proposal (TODO(Raman) to
confirm). Show unknown event types as plain text so a new type does not break the screen.

## Statuses a screen has to handle

**Proposal `status`**

| Value | Meaning | What the screen should do |
|---|---|---|
| `PENDING_REVIEW` | A fix is waiting for a manager. | Show the fix, citations, source badge and the Approve and Reject buttons. |
| `APPROVED` | A manager approved it. The fix is applied. | Read-only. Show the decision and comment. |
| `REJECTED` | A manager rejected it. Nothing changed. | Read-only. Show the decision and comment. |
| `ESCALATED` | The AI could not safely propose a fix. | Mark clearly: "AI couldn't safely propose a fix, manager review needed." Do not offer Approve. TODO(Raman, Priyanshu): decide how a manager closes an escalated case. |

An escalated proposal always has `fix.action = FLAG_FOR_MANAGER` and no changes. Do not assume
it has no citations: the low-confidence case keeps its citation and `source: LLM`.
The reason for an escalation (`NO_POLICY_MATCH`, `NO_BASELINE_FIX`, `BASELINE_FIX_INVALID`,
`LOW_CONFIDENCE`) is in the `payload.reason` of the `ESCALATED` audit event. The proposal's
`rationale` also says it in words.

**Exception `status`**: `OPEN` (found, no proposal yet, or the last one was rejected),
`PROPOSED` (a proposal waits for review), `ESCALATED` (needs a human, no AI fix),
`RESOLVED` (a fix was approved). In this mock data a rejected exception goes back to `OPEN`.
That is a guess, not agreed. TODO(Raman).

**Source**: `LLM` or `RULES_FALLBACK`.

## Proposed API

**Proposed, the backend owner (Raman) may change all of this.** The paths come from
CLAUDE.md section 9. The response shapes are my suggestion and are in `types.ts` under
"PROPOSED API shapes". Errors use the shape from CLAUDE.md: `{ "error": { "code", "message",
"request_id" } }`.

| What | Request | Response |
|---|---|---|
| List cases | `GET /api/v1/exceptions?status=&type=` | `CaseListItem[]` (like `cases.json`) |
| Get one case | `GET /api/v1/exceptions/{exception_id}` | `CaseDetail` (like `case_detail_*.json`) |
| Approve | `POST /api/v1/proposals/{proposal_id}/decision` with `{ "decision": "APPROVE", "manager_id": "...", "comment": "..." }` | the updated `CaseDetail` |
| Reject | the same endpoint with `"decision": "REJECT"` | the updated `CaseDetail` |
| Audit trail (optional, since the detail has it) | `GET /api/v1/audit?entity_id=` | `AuditEvent[]` |

Approve and reject share one endpoint in CLAUDE.md, so there is no separate path for each.

## Open questions

1. **Employee names.** The contract only has `employee_id`. If the screens need a name, the
   backend needs to provide one (a field or a lookup). TODO(Raman).
2. **Who is the manager.** `manager_id` has to come from somewhere (login). Not defined yet.
3. **Sorting and paging** of the case list. Not defined yet.
4. **`field` labels and `evidence` keys** are not fixed lists. TODO(Ankit).
5. **Time zone.** All times are UTC. The company is in `Asia/Kolkata`, and day boundaries are
   computed there, so display times in that zone.
