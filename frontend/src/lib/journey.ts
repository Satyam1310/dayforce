// Where a case is in the safety harness, step by step (shown by components/CaseJourney).
import { formatPercent } from './format'
import type { Decision, DetectedException, Proposal } from '../types/api'

export type StepState = 'done' | 'current' | 'warn' | 'blocked' | 'idle'

export interface Step {
  title: string
  detail: string
  state: StepState
}

export const STATE_LABEL: Record<StepState, string> = {
  done: 'done',
  current: 'in progress',
  warn: 'done with a caveat',
  blocked: 'stopped',
  idle: 'not reached',
}

export function buildJourney(
  exception: DetectedException,
  proposal: Proposal | null,
  decision: Decision | null | undefined,
): Step[] {
  const found: Step = {
    title: 'Found by rules',
    detail: exception.policy_refs.join(', ') || 'Rule check',
    state: 'done',
  }

  if (!proposal) {
    return [
      found,
      { title: 'Policy matched', detail: 'After a proposal', state: 'idle' },
      { title: 'Fix proposed', detail: 'Not proposed yet', state: 'current' },
      { title: 'Validator', detail: 'Not run yet', state: 'idle' },
      { title: 'Your decision', detail: 'Nothing to decide yet', state: 'idle' },
    ]
  }

  const top = proposal.citations[0]
  const policy: Step = top
    ? { title: 'Policy matched', detail: `${top.clause_id}, ${formatPercent(top.score)} match`, state: 'done' }
    : { title: 'Policy matched', detail: 'No clause close enough', state: 'warn' }

  const escalated = proposal.status === 'ESCALATED'
  const fix: Step = escalated
    ? { title: 'Fix proposed', detail: 'No safe fix, escalated', state: 'blocked' }
    : proposal.source === 'LLM'
      ? { title: 'Fix proposed', detail: `AI, ${formatPercent(proposal.confidence)} confident`, state: 'done' }
      : { title: 'Fix proposed', detail: 'AI unusable, rule-based fix', state: 'warn' }

  const validation = proposal.validation
  const validator: Step = !validation
    ? { title: 'Validator', detail: 'Nothing to check', state: 'idle' }
    : validation.passed
      ? { title: 'Validator', detail: 'Passed', state: 'done' }
      : { title: 'Validator', detail: `${validation.violations.length} problems found`, state: 'blocked' }

  const who = decision ? ` by ${decision.manager_id}` : ''
  const human: Step =
    proposal.status === 'PENDING_REVIEW'
      ? { title: 'Your decision', detail: 'Waiting for you', state: 'current' }
      : proposal.status === 'APPROVED'
        ? { title: 'Your decision', detail: `Approved${who}`, state: 'done' }
        : proposal.status === 'REJECTED'
          ? { title: 'Your decision', detail: `Rejected${who}`, state: 'warn' }
          : { title: 'Your decision', detail: 'Resolve it directly', state: 'current' }

  return [found, policy, fix, validator, human]
}
