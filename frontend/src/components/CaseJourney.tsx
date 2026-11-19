// The case's path through the safety harness, from detection to the manager's decision.
// Each step says what actually happened for THIS case, so a reviewer (or a judge) sees at
// a glance where the AI was used, where it was checked, and that a human has the last word.
import { buildJourney, STATE_LABEL } from '../lib/journey'
import type { Decision, DetectedException, Proposal } from '../types/api'

interface Props {
  exception: DetectedException
  proposal: Proposal | null
  decision?: Decision | null
}

export function CaseJourney({ exception, proposal, decision }: Props) {
  const steps = buildJourney(exception, proposal, decision)
  return (
    <ol className="journey" aria-label="How this case moved through the checks">
      {steps.map((step, i) => (
        <li key={step.title} className={`journey-step is-${step.state}`}>
          <span className="journey-dot" aria-hidden="true">
            {step.state === 'done' ? '✓' : step.state === 'blocked' ? '!' : i + 1}
          </span>
          <span className="journey-title">{step.title}</span>
          <span className="journey-detail">
            {step.detail}
            <span className="visually-hidden"> ({STATE_LABEL[step.state]})</span>
          </span>
        </li>
      ))}
    </ol>
  )
}
