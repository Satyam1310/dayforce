# Study guide (for Harsh)

Read this once top to bottom, then open the files named in it. It describes the code on `main`.
Things that exist only in an open pull request are marked **(open PR)**. Function names are given
instead of line numbers because line numbers move.

## 1. Words you will need

- **Contract**: a shared data shape (a Pydantic model) that every module agrees on. Pydantic checks the data matches the shape and raises an error if it does not.
- **Exception**: a problem found on a timecard (for example, a clock-in with no clock-out).
- **Baseline fix**: the fix the rules themselves work out. It is the safety net when the model fails.
- **Proposal**: a suggested fix for one exception, from the model or from the baseline.
- **Citation**: a policy clause id (like `POL-TA-01`) the fix relies on.
- **RAG** (retrieval-augmented generation): look up the relevant policy text first, then give it to the model, so it can cite real text.
- **Embedding**: a list of 384 numbers that stands for the meaning of a text. Texts with similar meaning have similar numbers. **Cosine similarity** measures how similar two such lists are (0 to 1 here).
- **Protocol** (Python): "anything with these methods". `Retriever` is a Protocol, so the in-memory and pgvector versions are interchangeable.
- **Escalate**: give the case to a person with no AI fix.
- **Paid time**: time worked minus logged unpaid breaks.

## 2. File map of `backend/app`

| File | What it does, in one line |
|---|---|
| `contracts/models.py` | The shared data shapes: `Timecard`, `Punch`, `Shift`, `DetectedException`, `Fix`, `Proposal`, `Decision`, `AuditEvent`, and the enums. Changes need a `contract-change` issue. |
| `contracts/policy_config.py` | The policy numbers (8 h, 12 h, 38 h, 40 h, 6 h, 30 min, 10 h) in one place. |
| `domain/rules/analysis.py` | Shared helpers: pairs punches into work blocks (`analyze_punches`), finds the scheduled shift (`scheduled_shift_for`), builds each employee's blocks (`employee_blocks`), the week start. |
| `domain/rules/detectors.py` | `detect_exceptions`: the four detectors (missed punch, daily and weekly overtime, schedule conflict, meal break, daily maximum, rest). No AI. |
| `validator/validator.py` | `validate_proposal`: applies a fix to a copy of the timecard, re-runs the detectors, and returns the violations. |
| `ai/settings.py` | Settings read from environment variables, plus `FALLBACK_CONFIDENCE` (0.75), the 2-call budget and the 25 s deadline. |
| `ai/prompts.py` | The prompts, the shape the model must answer in (`LLMProposal`), `sanitize_notes`, `parse_llm_output`. |
| `ai/llm.py` | `OpenAICompatibleLLM` (Groq or any OpenAI-style endpoint) and `MockLLM` (a fake with failure modes), `build_llm`. |
| `ai/retrieval.py` | `build_query`, `InMemoryRetriever`, `PgVectorRetriever`, `get_retriever`. |
| `ai/embeddings.py`, `ai/ingest.py` | The embedding model wrapper, and the command that loads the policy clauses into the database. |
| `ai/workflow.py` | `run_workflow` (the state machine) and `generate_proposal` (the public entry point). |
| `scripts/smoke_llm.py` | Runs a fixed set of cases against one or more models and reports rates and latency. Throttled. |
| `scripts/demo_flow.py` | A narrated run of one case, for the demo video. |
| `scripts/export_mock_data.py` | Builds the example data and TypeScript types for the frontend from the real models. |
| `api/`, `db/`, `main.py`, `services/`, `observability/` **(open PR #12)** | The API, Postgres tables, applying an approved fix, logging and metrics. Not on `main` yet. |

Outside `backend/app`: `data/policies/time_attendance_policy.md` (the policy text),
`docs/frontend-mock/` (data for the UI), `frontend/` (React screens).

## 3. The path one timecard takes

Take the fixture timecard: someone clocked in at 09:02 and never clocked out, with a 09:00 to
18:00 shift.

1. **The data.** A `Timecard` (`contracts/models.py`) holds a list of `Punch` and `Shift` objects
   and a `notes` string.
2. **Detection.** `detect_exceptions` (`detectors.py`) groups timecards by employee, then calls
   `analyze_punches` (`analysis.py`) to pair clock-ins with clock-outs into `WorkBlock`s. Our
   clock-in has no partner, so `_missed_punch` picks up the unmatched clock-in. It calls
   `scheduled_shift_for` to find the 09:00 to 18:00 shift, checks the shift would be 12 hours or
   less, and builds the baseline `Fix`: add a `punch.OUT` at 18:00. `_make` wraps it into a
   `DetectedException` with an id made from its content, so the same input always gives the same id.
3. **Entry to the AI part.** Something calls `generate_proposal(exc, timecard)` (`workflow.py`). It
   reads settings, picks a retriever with `get_retriever` and a model with `build_llm`, and calls
   `run_workflow` with `validate_proposal` as the validator.
4. **Retrieve policy.** `build_query(exc)` (`retrieval.py`) makes a search text for the rule that
   fired. `retriever.retrieve(query, k=3)` returns the best 3 clauses with scores. Anything under
   the minimum score is dropped. If nothing is left, the case is escalated (`NO_POLICY_MATCH`).
5. **Ask the model.** `build_user_prompt` (`prompts.py`) puts the exception, the timecard facts
   and the clauses into the prompt. It calls `sanitize_notes` so the notes arrive cleaned,
   escaped and wrapped in `<untrusted_notes>`. `llm.complete(...)` sends it, with a timeout equal
   to the time left of the 25 seconds.
6. **Check the answer.** `parse_llm_output` turns the reply into an `LLMProposal` (or fails). Then
   the citation check: every cited id must be among the retrieved clauses. Then
   `validate_proposal` (`validator.py`): it routes to `_check_add_punch`, which checks the field,
   the time (must equal the scheduled end, at most 12 hours, enough rest before the next shift),
   then `_rerun` applies the fix to a copy and calls `detect_exceptions` on the copy to confirm the
   problem is gone and nothing new appeared.
7. **If anything fails.** It counts against the shared budget of 2 model calls. The validator's
   reasons are fed into the second prompt. When the budget is used up, or the model errored, the
   `fallback()` inside `run_workflow` takes `exc.baseline_fix`, validates it too, and uses it
   (source `RULES_FALLBACK`). If there is no baseline, or it fails validation, `escalate()` returns
   a `FLAG_FOR_MANAGER` fix with no changes.
8. **Confidence.** `_finish` escalates if confidence is under 0.5. Otherwise it builds the
   `Proposal` with status `PENDING_REVIEW`.
9. **Result.** `run_workflow` returns `(proposal, events)`: one `AuditEvent` per step
   (`WORKFLOW_STARTED`, `POLICY_RETRIEVED`, `LLM_CALLED`, `VALIDATION_RESULT`, and so on). It stores nothing.
10. **After that (open PR #12).** The API saves the proposal and events. A manager approves or
    rejects. On approval `apply_fix` changes the timecard, and the decision and the change are
    saved together, with audit events. On rejection nothing changes.

## 4. The 12 rule decisions, in plain words

These are the judgement calls in the detectors and validator (PR #11). Each can be argued; know
the reason.

1. **Overtime counts paid time** (breaks taken out). Shift length, meal break and rest use the
   clock span. *Why:* overtime is about hours paid. Rest and shift length are about the clock.
2. **8 to 12 paid hours is a low-severity overtime note with no change needed. Over 12 is a high
   breach with no automatic fix**, and is not also reported as overtime. *Why:* the policy
   says over 12 hours needs a manager.
3. **Weekly risk** = paid hours on days with punches plus scheduled shifts on days with none; 38 or
   more is flagged (high above 40). A day with a missing punch counts as zero worked, and is
   counted in the evidence. *Why:* we need a projection, and we cannot know hours that were never
   recorded. The total can therefore be too low.
4. **Conflicts keep the shift that starts first.** *Why:* the policy says "earliest assigned", but
   the contract has no assignment time. This is a stand-in.
5. **A shift can only be taken off an employee and left open.** *Why:* we cannot check the other
   employee's hours or rest.
6. **Rest is measured between work blocks.** Two clock-in/out pairs in one day are two shifts, so a
   split shift with a short gap breaches rest. Back-to-back shifts breach it. Overlaps are
   conflicts, not rest breaches. *Why:* the policy says between the end of one shift and the start
   of the next.
7. **Meal break:** a block over 6 hours with under 30 logged minutes. The deduct-30 fix is offered
   only when no break was logged at all. Notes like "I took no break" are not read. *Why:* a
   partial break is a judgement for a manager, and notes are untrusted.
8. **The clock-out can only be set to the scheduled shift end**, and the validator requires exactly
   that. No fix without a schedule, if the shift end is before the clock-in, or if it passes 12
   hours. A clock-out with no clock-in has no fix. *Why:* this is what the policy says, and it
   means a time written in the notes cannot move the clock-out.
9. **A break deduction must equal the exact missing minutes.** `NO_CHANGE` is allowed only for
   overtime. `EDIT_PUNCH` is not allowed at all. *Why:* docking too much or too little is wrong,
   and the policy never lets us edit an existing punch.
10. **A meal-break violation that only appears once a missed punch is fixed is not blamed on that
    fix.** Going over 12 hours or under 10 hours of rest is blamed on it, so such a fix fails and
    the case escalates. *Why:* an unfinished day cannot be judged for breaks, but the hard limits
    must hold.
11. **"Edits need manager approval"** is `requires_manager_approval(fix)` plus status
    `PENDING_REVIEW`. *Why:* the contract has no field for it, and a contract change was not
    needed.
12. **Overnight and day boundaries:** no date is taken from a timestamp. A shift stays on its
    timecard's `work_date`, hours are not split at midnight, weeks start on Monday from
    `work_date`, and a timestamp with no timezone is read as UTC. *Why:* the data source assigns
    the day in Kolkata time, so we trust it instead of guessing.

## 5. Fifteen questions an interviewer could ask

**1. Walk me through what happens to one timecard.** Section 3. Detection (code), retrieval,
model, parse, citation check, validator, fallback or escalation, confidence check, proposal,
then a manager.

**2. Why not just ask the model to fix timecards?** A model alone can be confidently wrong, and
this touches pay. Code finds the problem, the model only suggests, code checks, a person decides.

**3. What stops the model inventing policy?** It only returns clause ids. We accept an id only if
that clause was retrieved above the minimum score. The quote shown is copied from our text.

**4. What if an employee writes "approve my overtime" in the notes?** The notes are data: cleaned,
escaped, cut to 500 characters and wrapped. The detectors never read them. And even if the model
obeys, its fix goes through the same validator, which rejects it. In our tests, 0 of 5 attacks got
through (PR #15, merged). We do not claim immunity.

**5. What if the model is down or rate limited?** An error, timeout or 429 falls back at once to
the rule-based fix. If there is none, a manager gets the case. At most 2 calls and 25 seconds.

**6. How do you know the detectors are right?** Unit tests on every boundary (exactly 8, 12, 6
hours, 38 and 40 weekly hours, 10 hours of rest), property tests on random timecards, and a golden
set of 40 cases whose expected results we wrote from the policy (PR #15, merged). It is our own
reading of the policy on cases we wrote, so it proves consistency, not that we are right.

**7. Where did your evaluation find a real bug?** Retrieval used one query per exception type. For
daily overtime the right clause scored 0.33 against a 0.35 minimum, so a correct citation was
rejected. Fixed by a query per rule. The citation check did its job; our search was wrong.

**8. Why a threshold of 0.57?** We measured 8 relevant and 8 off-topic queries: relevant scored
0.629 to 0.878, off-topic 0.406 to 0.511, so 0.57 is in the middle of the gap. It is a small
sample, so we would re-check it with real queries.

**9. What does the validator not check?** It sees one timecard at a time. Rest against neighbouring
days and later shifts come from facts the detector stored. A fix that cites anything else is
rejected as unverifiable. It does not check another employee's hours, because we never give a
shift to another employee.

**10. Why did you not use LangGraph?** The flow is short and linear, and a plain state machine was
easy to test on every path. The spec lists LangGraph, so a version is still on the list.

**11. What does the model's confidence number mean?** It is the model's own estimate, uncalibrated.
We use it only as a coarse net: under 0.5 goes to a manager. The 0.75 on rule-based fixes is a
fixed value we chose, not a measurement.

**12. How does it scale?** The workflow keeps no state, so several copies can run side by side.
The limit is the model: the free tier allows about 8,000 tokens a minute. None of the scaling is
deployed yet; see Satyam's Docker and Kubernetes PRs when they exist.

**13. What are the privacy risks?** A hosted model sees employee ids, punch times and notes. We
have an offline mode with a local model, but we did no privacy review.

**14. How much was written by an AI assistant?** A lot of the first drafts of code and docs.
The team set the architecture, the contracts, the policy and the safety rules, and every module is
tested. Teammate review of several pull requests is still to be done.

**15. What would you do differently?** Put the evaluation in earlier, because it found the
retrieval bug. Agree the API paths with the contract spec before building it, because the API now
differs from the spec. And test the real model together with the real validator, which we have not
done yet.
