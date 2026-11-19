import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { Empty, ErrorState, Loading } from '../components/States'
import { useAsync } from '../hooks/useAsync'
import { usePageTitle } from '../hooks/usePageTitle'
import { describeValue } from '../lib/describe'
import { formatDateTime, humanize } from '../lib/format'

function actorClass(actor: string): string {
  if (actor === 'system') return 'actor actor-system'
  if (actor === 'llm') return 'actor actor-llm'
  return 'actor actor-manager'
}

/** Event details as short label/value pairs instead of raw JSON.
 *  Values are rendered as plain text (they can contain untrusted comments). */
function Payload({ payload }: { payload: Record<string, unknown> }) {
  const entries = Object.entries(payload).filter(
    ([, v]) => v !== null && v !== undefined && v !== '',
  )
  if (entries.length === 0) return <span className="hint">No details</span>
  return (
    <dl className="kv">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{humanize(key)}</dt>
          <dd className={key === 'comment' ? 'kv-quote' : undefined}>
            {key === 'comment' ? `“${String(value)}”` : formatDetail(value)}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function formatDetail(value: unknown): string {
  if (typeof value === 'number')
    return value <= 1 && value > 0 ? `${Math.round(value * 100)}%` : String(value)
  if (Array.isArray(value)) {
    return (
      value
        .map((v) =>
          typeof v === 'object' && v && 'clause_id' in v
            ? String((v as { clause_id: string }).clause_id)
            : describeValue(v),
        )
        .join(', ') || 'none'
    )
  }
  if (typeof value === 'object') return Object.keys(value as object).length + ' fields'
  return describeValue(value)
}

export function AuditLogPage() {
  const [params, setParams] = useSearchParams()
  usePageTitle('Audit log')
  const entityId = params.get('entity_id') ?? ''
  const [input, setInput] = useState(entityId)
  const { data, error, loading, reload } = useAsync(
    () => api.listAudit(entityId || undefined),
    [entityId],
  )

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = input.trim()
    setParams(trimmed ? { entity_id: trimmed } : {})
  }

  return (
    <section>
      <div className="page-head">
        <div>
          <h1>Audit log</h1>
          <p className="muted">Every detection, proposal and decision, with who did it and when.</p>
        </div>
      </div>

      <form className="filters" onSubmit={onSubmit}>
        <label>
          Entity ID
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="exception, proposal or timecard ID"
            size={40}
          />
        </label>
        <button type="submit" className="btn">
          Filter
        </button>
        {entityId && (
          <button
            type="button"
            className="btn btn-link"
            onClick={() => {
              setInput('')
              setParams({})
            }}
          >
            Show all
          </button>
        )}
      </form>

      {error && <ErrorState error={error} onRetry={reload} />}
      {!error && !data && loading && <Loading label="Loading audit log…" />}
      {!error && data && data.length === 0 && <Empty>No audit events found.</Empty>}
      {!error && data && data.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>When</th>
              <th>Actor</th>
              <th>Event</th>
              <th>Entity</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {data.map((ev) => {
              const exceptionId =
                ev.entity_type === 'exception'
                  ? ev.entity_id
                  : typeof ev.payload.exception_id === 'string'
                    ? ev.payload.exception_id
                    : null
              return (
                <tr key={ev.id}>
                  <td className="nowrap">{formatDateTime(ev.ts)}</td>
                  <td>
                    <span className={actorClass(ev.actor)}>{ev.actor}</span>
                  </td>
                  <td>{humanize(ev.event_type)}</td>
                  <td className="small">
                    {ev.entity_type}
                    <div className="muted mono" title={ev.entity_id}>
                      {ev.entity_id.slice(0, 8)}…
                    </div>
                    {exceptionId && <Link to={`/exceptions/${exceptionId}`}>Open exception</Link>}
                  </td>
                  <td>
                    {/* payload may contain untrusted text: React renders it as plain text */}
                    <Payload payload={ev.payload} />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </section>
  )
}
