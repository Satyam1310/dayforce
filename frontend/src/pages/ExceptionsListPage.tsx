import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { StatusBadge } from '../components/Badges'
import { Empty, ErrorState, Loading } from '../components/States'
import { useAsync } from '../hooks/useAsync'
import { formatDate, formatPercent, humanize, shortId } from '../lib/format'
import { summarize } from '../lib/summary'
import {
  EXCEPTION_STATUSES,
  EXCEPTION_TYPES,
  type CaseListItem,
  type ExceptionStatus,
  type ExceptionType,
  type Proposal,
  type Severity,
} from '../types/api'

const SEVERITY_ORDER: Record<Severity, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 }

// Status tabs, in the order a manager works through them.
const TABS: { status: ExceptionStatus | undefined; label: string }[] = [
  { status: 'PROPOSED', label: 'Awaiting review' },
  { status: 'ESCALATED', label: 'Escalated' },
  { status: 'OPEN', label: 'Open' },
  { status: 'RESOLVED', label: 'Resolved' },
  { status: undefined, label: 'All' },
]

// Highest severity first, then most recent day first.
function sortCases(list: CaseListItem[]): CaseListItem[] {
  return [...list].sort(
    (a, b) =>
      SEVERITY_ORDER[a.exception.severity] - SEVERITY_ORDER[b.exception.severity] ||
      b.exception.work_date.localeCompare(a.exception.work_date),
  )
}

/** What the manager needs to know about the fix, in a few words. */
function ProposalCell({ proposal }: { proposal: Proposal | null | undefined }) {
  if (!proposal) return <span className="fix-cell is-none">No fix yet</span>
  switch (proposal.status) {
    case 'PENDING_REVIEW':
      return proposal.source === 'LLM' ? (
        <span className="fix-cell is-ready">
          AI fix ready, {formatPercent(proposal.confidence)}
        </span>
      ) : (
        <span className="fix-cell is-ready">Rule-based fix ready</span>
      )
    case 'ESCALATED':
      return <span className="fix-cell is-blocked">No safe fix</span>
    case 'APPROVED':
      return <span className="fix-cell is-done">Fix applied</span>
    case 'REJECTED':
      return <span className="fix-cell is-none">Fix rejected</span>
  }
}

export function ExceptionsListPage() {
  // Filters live in the URL (?type=...&status=...) so a filtered view can be shared.
  const [params, setParams] = useSearchParams()
  const type = EXCEPTION_TYPES.find((t) => t === params.get('type'))
  const status = EXCEPTION_STATUSES.find((s) => s === params.get('status'))

  // Type is filtered by the server; status is filtered here so every tab can show a count.
  const { data, error, loading, reload } = useAsync(() => api.listCases({ type }), [type])

  function setFilter(key: 'type' | 'status', value: string | undefined) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }

  const count = (s: ExceptionStatus | undefined) =>
    data?.filter((c) => !s || c.exception.status === s).length ?? 0
  const waiting = data?.filter((c) => c.proposal?.status === 'PENDING_REVIEW').length ?? 0
  const escalated = count('ESCALATED')
  const rows = data ? sortCases(data.filter((c) => !status || c.exception.status === status)) : []

  return (
    <section>
      <header className="queue-head">
        <h1>
          {!data
            ? 'Review queue'
            : waiting === 0
              ? 'Nothing is waiting for your decision'
              : `${waiting} ${waiting === 1 ? 'fix is' : 'fixes are'} waiting for your decision`}
        </h1>
        <p className="lede">
          {escalated > 0 && (
            <>
              {escalated} escalated {escalated === 1 ? 'case needs' : 'cases need'} you to resolve
              {escalated === 1 ? ' it' : ' them'} directly.{' '}
            </>
          )}
          Rule checks find the problems, the AI suggests fixes, and nothing changes on a timecard
          until you approve it.
        </p>
      </header>

      <div className="toolbar">
        <div className="tabs" role="group" aria-label="Status">
          {TABS.map((tab) => (
            <button
              key={tab.label}
              type="button"
              className="tab"
              aria-pressed={status === tab.status}
              onClick={() => setFilter('status', tab.status)}
            >
              {tab.label}
              {data && <span className="tab-count">{count(tab.status)}</span>}
            </button>
          ))}
        </div>
        <label className="type-filter">
          <span>Type</span>
          <select
            aria-label="Type"
            value={type ?? ''}
            onChange={(e) => setFilter('type', e.target.value || undefined)}
          >
            <option value="">All types</option>
            {EXCEPTION_TYPES.map((t: ExceptionType) => (
              <option key={t} value={t}>
                {humanize(t)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && <ErrorState error={error} onRetry={reload} />}
      {!error && !data && loading && <Loading label="Loading the queue…" />}
      {!error && data && rows.length === 0 && (
        <Empty>
          No exceptions match these filters. Nothing to review.{' '}
          <button type="button" className="btn btn-link" onClick={() => setParams({})}>
            Show all cases
          </button>
        </Empty>
      )}
      {!error && rows.length > 0 && (
        <table className="table queue">
          <thead>
            <tr>
              <th>Case</th>
              <th>Employee</th>
              <th>Work date</th>
              <th>Fix</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ exception: e, proposal }) => (
              <tr key={e.id} className={`sev-row sev-row-${e.severity.toLowerCase()}`}>
                <td>
                  <Link to={`/exceptions/${e.id}`} className="case-link">
                    {humanize(e.type)}
                  </Link>
                  <span className="case-summary">{summarize(e)}</span>
                  <span className="case-severity">{humanize(e.severity)} severity</span>
                </td>
                <td title={e.employee_id}>Employee {shortId(e.employee_id)}</td>
                <td className="nowrap">{formatDate(e.work_date)}</td>
                <td>
                  <ProposalCell proposal={proposal} />
                </td>
                <td>
                  <StatusBadge status={e.status} />
                  {e.status === 'ESCALATED' && <span className="needs-manager">Needs manager</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
