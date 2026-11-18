import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { SeverityBadge, StatusBadge } from '../components/Badges'
import { Empty, ErrorState, Loading } from '../components/States'
import { useAsync } from '../hooks/useAsync'
import { formatDate, humanize, shortId } from '../lib/format'
import {
  EXCEPTION_STATUSES,
  EXCEPTION_TYPES,
  type DetectedException,
  type ExceptionStatus,
  type ExceptionType,
  type Severity,
} from '../types/api'

const SEVERITY_ORDER: Record<Severity, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 }

// Highest severity first, then most recent day first.
function sortExceptions(list: DetectedException[]): DetectedException[] {
  return [...list].sort(
    (a, b) =>
      SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] ||
      b.work_date.localeCompare(a.work_date),
  )
}

export function ExceptionsListPage() {
  // Filters live in the URL (?type=...&status=...) so a filtered view can be shared.
  const [params, setParams] = useSearchParams()
  const typeParam = params.get('type')
  const statusParam = params.get('status')
  const type = EXCEPTION_TYPES.find((t) => t === typeParam)
  const status = EXCEPTION_STATUSES.find((s) => s === statusParam)

  const { data, error, loading, reload } = useAsync(
    () => api.listExceptions({ type, status }),
    [type, status],
  )

  function setFilter(key: 'type' | 'status', value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }

  const needsReview = data?.filter((e) => e.status === 'PROPOSED').length ?? 0
  const escalated = data?.filter((e) => e.status === 'ESCALATED').length ?? 0

  return (
    <section>
      <div className="page-head">
        <div>
          <h1>Timecard exceptions</h1>
          <p className="muted">
            Problems found by the rule checks. Review each AI-proposed fix before it changes a timecard.
          </p>
        </div>
        {data && (
          <div className="summary">
            <span>
              <strong>{needsReview}</strong> awaiting review
            </span>
            <span>
              <strong>{escalated}</strong> escalated
            </span>
          </div>
        )}
      </div>

      <div className="filters">
        <label>
          Type
          <select aria-label="Type" value={type ?? ''} onChange={(e) => setFilter('type', e.target.value)}>
            <option value="">All types</option>
            {EXCEPTION_TYPES.map((t: ExceptionType) => (
              <option key={t} value={t}>
                {humanize(t)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select aria-label="Status" value={status ?? ''} onChange={(e) => setFilter('status', e.target.value)}>
            <option value="">All statuses</option>
            {EXCEPTION_STATUSES.map((s: ExceptionStatus) => (
              <option key={s} value={s}>
                {humanize(s)}
              </option>
            ))}
          </select>
        </label>
        {(type || status) && (
          <button type="button" className="btn btn-link" onClick={() => setParams(new URLSearchParams())}>
            Clear filters
          </button>
        )}
      </div>

      {error && <ErrorState error={error} onRetry={reload} />}
      {!error && !data && loading && <Loading label="Loading exceptions…" />}
      {!error && data && data.length === 0 && (
        <Empty>No exceptions match these filters. Nothing to review.</Empty>
      )}
      {!error && data && data.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Severity</th>
              <th>Exception</th>
              <th>Employee</th>
              <th>Work date</th>
              <th>Policy</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {sortExceptions(data).map((e) => (
              <tr key={e.id} className={e.status === 'ESCALATED' ? 'row-escalated' : undefined}>
                <td>
                  <SeverityBadge severity={e.severity} />
                </td>
                <td>
                  <Link to={`/exceptions/${e.id}`}>{humanize(e.type)}</Link>
                </td>
                <td title={e.employee_id}>Employee {shortId(e.employee_id)}</td>
                <td>{formatDate(e.work_date)}</td>
                <td className="small">{e.policy_refs.join(', ')}</td>
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
