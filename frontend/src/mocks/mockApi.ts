// In-browser fake backend. Behaves like the real API (CLAUDE.md section 9)
// closely enough to build and test the UI: approving a proposal really
// changes its status and writes audit events, until the page is reloaded.
import { ApiError, type Api } from '../api/types'
import type { AuditEvent, Decision, Proposal } from '../types/api'
import { createSeed, type MockDb } from './data'

let db: MockDb = createSeed()

/** Reset to the seed data. Used by tests so each test starts clean. */
export function resetMockDb(): void {
  db = createSeed()
}

// Small fake delay so loading states are visible in the browser (none in tests).
const LATENCY_MS = import.meta.env.MODE === 'test' ? 0 : 300
const delay = () => new Promise((r) => setTimeout(r, LATENCY_MS))

const clone = <T>(value: T): T => structuredClone(value)
const newId = () => crypto.randomUUID()
const now = () => new Date().toISOString()

function notFound(what: string, id: string): never {
  throw new ApiError(404, 'NOT_FOUND', `${what} ${id} not found`, `mock-${newId()}`)
}

function audit(e: Omit<AuditEvent, 'id' | 'ts'>): void {
  db.audit.push({ id: newId(), ts: now(), ...e })
}

function latestProposalFor(exceptionId: string): Proposal | null {
  const all = db.proposals.filter((p) => p.exception_id === exceptionId)
  return all.length ? all[all.length - 1] : null
}

export const mockApi: Api = {
  async listCases(filters) {
    await delay()
    return clone(
      db.exceptions
        .filter(
          (e) =>
            (!filters.status || e.status === filters.status) &&
            (!filters.type || e.type === filters.type),
        )
        .map((exception) => ({ exception, proposal: latestProposalFor(exception.id) })),
    )
  },

  async getExceptionDetail(id) {
    await delay()
    const exception = db.exceptions.find((e) => e.id === id) ?? notFound('Exception', id)
    const timecard =
      db.timecards.find((t) => t.id === exception.timecard_id) ??
      notFound('Timecard', exception.timecard_id)
    const proposal = latestProposalFor(id)
    const decision = proposal ? (db.decisions.find((d) => d.proposal_id === proposal.id) ?? null) : null
    return clone({ exception, timecard, proposal, decision })
  },

  // The real backend runs the LangGraph AI workflow here. The mock just uses
  // the rule-based baseline fix, or escalates when there isn't one.
  async proposeFix(exceptionId) {
    await delay()
    const exception =
      db.exceptions.find((e) => e.id === exceptionId) ?? notFound('Exception', exceptionId)

    const proposal: Proposal = exception.baseline_fix
      ? {
          id: newId(),
          exception_id: exceptionId,
          fix: clone(exception.baseline_fix),
          rationale: 'Rule-based fix from the detector (mock API: no AI call was made).',
          citations: [],
          confidence: 0.7,
          source: 'RULES_FALLBACK',
          model: null,
          prompt_version: 'v1',
          validation: { passed: true, violations: [] },
          status: 'PENDING_REVIEW',
        }
      : {
          id: newId(),
          exception_id: exceptionId,
          fix: { action: 'FLAG_FOR_MANAGER', changes: [] },
          rationale:
            'AI could not safely propose a fix: there is no schedule for this day, so the policy says escalate. Manager review needed.',
          citations: [],
          confidence: 0,
          source: 'RULES_FALLBACK',
          model: null,
          prompt_version: 'v1',
          validation: null,
          status: 'ESCALATED',
        }

    db.proposals.push(proposal)
    exception.status = proposal.status === 'ESCALATED' ? 'ESCALATED' : 'PROPOSED'
    audit({
      actor: 'system',
      event_type: proposal.status === 'ESCALATED' ? 'EXCEPTION_ESCALATED' : 'PROPOSAL_FALLBACK_USED',
      entity_type: 'proposal',
      entity_id: proposal.id,
      payload: { exception_id: exceptionId, source: proposal.source, confidence: proposal.confidence },
    })
    return clone(proposal)
  },

  // Same rules as POST /api/cases/{id}/approve and /reject (backend/API.md).
  async decide(caseId, body) {
    await delay()
    const exception = db.exceptions.find((e) => e.id === caseId) ?? notFound('Case', caseId)
    const proposal = latestProposalFor(caseId)
    const fail = (code: string, message: string): never => {
      throw new ApiError(409, code, message, `mock-${newId()}`)
    }
    if (exception.status === 'ESCALATED' || proposal?.status === 'ESCALATED') {
      fail('CASE_ESCALATED', 'This case is escalated: there is nothing to approve.')
    }
    if (!proposal) return fail('NO_PROPOSAL', 'This case has no proposal yet.')
    if (proposal.status !== 'PENDING_REVIEW') {
      fail('ALREADY_DECIDED', `The proposal for this case is already ${proposal.status}.`)
    }
    if (body.decision === 'REJECT' && !body.comment?.trim()) {
      throw new ApiError(422, 'COMMENT_REQUIRED', 'A comment is required to reject.', `mock-${newId()}`)
    }
    const proposalId = proposal.id

    const decision: Decision = {
      id: newId(),
      proposal_id: proposalId,
      decision: body.decision,
      manager_id: body.manager_id,
      comment: body.comment,
      decided_at: now(),
    }
    db.decisions.push(decision)

    const approved = body.decision === 'APPROVE'
    proposal.status = approved ? 'APPROVED' : 'REJECTED'
    if (exception) exception.status = approved ? 'RESOLVED' : 'OPEN'

    audit({
      actor: body.manager_id,
      event_type: approved ? 'DECISION_APPROVED' : 'DECISION_REJECTED',
      entity_type: 'proposal',
      entity_id: proposalId,
      payload: { decision_id: decision.id, exception_id: proposal.exception_id, comment: body.comment },
    })

    // Only an approval changes the timecard (the real backend does this in one DB transaction).
    if (approved && exception) {
      const timecard = db.timecards.find((t) => t.id === exception.timecard_id)
      for (const change of proposal.fix.changes) {
        if (timecard && proposal.fix.action === 'ADD_PUNCH' && change.field === 'punch.OUT' && change.new) {
          timecard.punches.push({ id: newId(), employee_id: timecard.employee_id, type: 'OUT', timestamp: change.new })
        }
      }
      audit({
        actor: 'system',
        event_type: 'TIMECARD_UPDATED',
        entity_type: 'timecard',
        entity_id: exception.timecard_id,
        payload: { exception_id: exception.id, action: proposal.fix.action, changes: proposal.fix.changes.length },
      })
    }
    return mockApi.getExceptionDetail(caseId)
  },

  async listAudit(entityId) {
    await delay()
    const events = entityId ? db.audit.filter((e) => e.entity_id === entityId) : db.audit
    // newest first
    return clone([...events].sort((a, b) => b.ts.localeCompare(a.ts)))
  },
}
