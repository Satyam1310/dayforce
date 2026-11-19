// TypeScript copies of the shared contracts (CLAUDE.md section 8,
// backend/app/contracts/models.py). Keep these in sync with the backend:
// if a contract changes, change it here too.
//
// Dates/times arrive as ISO-8601 strings (UTC). We convert them to
// Asia/Kolkata only when displaying (see src/lib/format.ts).

export type PunchType = 'IN' | 'OUT' | 'BREAK_START' | 'BREAK_END'

export interface Punch {
  id: string
  employee_id: string
  type: PunchType
  timestamp: string
}

export interface Shift {
  id: string
  employee_id: string
  start: string
  end: string
  location_id: string
}

export interface Timecard {
  id: string
  employee_id: string
  work_date: string // "YYYY-MM-DD"
  punches: Punch[]
  shifts: Shift[]
  notes: string | null // UNTRUSTED text: always shown as plain text
}

export type ExceptionType = 'MISSED_PUNCH' | 'OVERTIME_RISK' | 'SCHEDULE_CONFLICT' | 'POLICY_BREACH'

export const EXCEPTION_TYPES: ExceptionType[] = [
  'MISSED_PUNCH',
  'OVERTIME_RISK',
  'SCHEDULE_CONFLICT',
  'POLICY_BREACH',
]

export type Severity = 'LOW' | 'MEDIUM' | 'HIGH'

export type FixAction =
  'ADD_PUNCH' | 'EDIT_PUNCH' | 'DEDUCT_BREAK' | 'REASSIGN_SHIFT' | 'FLAG_FOR_MANAGER' | 'NO_CHANGE'

export interface FieldChange {
  target_id: string
  field: string
  old: string | null
  new: string | null
}

export interface Fix {
  action: FixAction
  changes: FieldChange[]
}

export type ExceptionStatus = 'OPEN' | 'PROPOSED' | 'ESCALATED' | 'RESOLVED'

export const EXCEPTION_STATUSES: ExceptionStatus[] = ['OPEN', 'PROPOSED', 'ESCALATED', 'RESOLVED']

export interface DetectedException {
  id: string
  type: ExceptionType
  severity: Severity
  employee_id: string
  timecard_id: string
  work_date: string
  evidence: Record<string, unknown> // facts the detector found
  policy_refs: string[] // e.g. ["POL-TA-01"]
  baseline_fix: Fix | null // rule-based fix, used as the fallback
  status: ExceptionStatus
}

export interface PolicyCitation {
  clause_id: string
  quote: string
  score: number
}

export interface Violation {
  code: string
  message: string
  clause_id: string | null
}

export interface ValidationResult {
  passed: boolean
  violations: Violation[]
}

export type ProposalSource = 'LLM' | 'RULES_FALLBACK'

export type ProposalStatus = 'PENDING_REVIEW' | 'APPROVED' | 'REJECTED' | 'ESCALATED'

export interface Proposal {
  id: string
  exception_id: string
  fix: Fix
  rationale: string // max 400 chars
  citations: PolicyCitation[]
  confidence: number // 0..1
  source: ProposalSource
  model: string | null
  prompt_version: string
  validation: ValidationResult | null
  status: ProposalStatus
}

export type DecisionValue = 'APPROVE' | 'REJECT'

export interface Decision {
  id: string
  proposal_id: string
  decision: DecisionValue
  manager_id: string
  comment: string | null
  decided_at: string
}

export interface AuditEvent {
  id: string
  ts: string
  actor: string // "system" | "llm" | manager id
  event_type: string
  entity_type: string
  entity_id: string
  payload: Record<string, unknown>
}

// ---- Frontend-side shapes (not in models.py) ----

// GET /exceptions/{id} returns "one exception with its timecard, proposal and
// validation" (CLAUDE.md section 9). The exact JSON shape is not fixed yet;
// GET /api/cases/{id} returns CaseDetail (docs/frontend-mock/types.ts, backend/API.md).
export interface ExceptionDetail {
  exception: DetectedException
  timecard: Timecard
  proposal: Proposal | null
  decision?: Decision | null // the manager's decision, once there is one
  audit_events?: AuditEvent[]
}

// GET /api/cases returns CaseListItem[]
export interface CaseListItem {
  exception: DetectedException
  proposal?: Proposal | null
}

// Body for POST /proposals/{id}/decision
export interface DecisionRequest {
  decision: DecisionValue
  manager_id: string
  comment: string | null
}

// Shared error shape: { "error": { "code", "message", "request_id" } }
export interface ApiErrorBody {
  error: { code: string; message: string; request_id: string }
}
