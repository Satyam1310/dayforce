import { humanize } from '../lib/format'
import type { ExceptionStatus, ProposalSource, ProposalStatus, Severity } from '../types/api'

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={`badge sev-${severity.toLowerCase()}`}>{humanize(severity)}</span>
}

export function StatusBadge({ status }: { status: ExceptionStatus | ProposalStatus }) {
  return <span className={`badge status-${status.toLowerCase()}`}>{humanize(status)}</span>
}

/** Shows where a proposal came from: the AI, or the rule-based fallback. */
export function SourceBadge({ source }: { source: ProposalSource }) {
  return source === 'LLM' ? (
    <span className="badge source-llm" title="Suggested by the AI, checked by the validator">
      AI suggestion
    </span>
  ) : (
    <span
      className="badge source-fallback"
      title="The AI could not give a valid fix, so the rule-based fix is shown"
    >
      Rule-based fallback
    </span>
  )
}
