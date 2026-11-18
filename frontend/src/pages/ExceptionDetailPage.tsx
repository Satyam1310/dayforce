import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { SeverityBadge, SourceBadge, StatusBadge } from '../components/Badges'
import { CitationList } from '../components/CitationList'
import { DecisionForm } from '../components/DecisionForm'
import { FixDiff } from '../components/FixDiff'
import { ErrorState, Loading } from '../components/States'
import { Timeline } from '../components/Timeline'
import { ValidationPanel } from '../components/ValidationPanel'
import { useAsync } from '../hooks/useAsync'
import {
  formatDate,
  formatDateTime,
  formatPercent,
  formatValue,
  humanize,
  shortId,
} from '../lib/format'
import { POLICY_CLAUSES } from '../lib/policy'
import type { AuditEvent, ExceptionDetail } from '../types/api'

interface DetailWithHistory extends ExceptionDetail {
  history: AuditEvent[]
}

// Loads the exception and the audit events for it, its timecard and its proposal.
async function loadDetail(id: string): Promise<DetailWithHistory> {
  const detail = await api.getExceptionDetail(id)
  const ids = [detail.exception.id, detail.timecard.id, detail.proposal?.id].filter(
    (x): x is string => Boolean(x),
  )
  const lists = await Promise.all(ids.map((entityId) => api.listAudit(entityId)))
  const history = lists.flat().sort((a, b) => b.ts.localeCompare(a.ts))
  return { ...detail, history }
}

export function ExceptionDetailPage() {
  const { id = '' } = useParams()
  // key={id}: moving to another exception starts with fresh page state
  return <ExceptionDetailView key={id} id={id} />
}

function ExceptionDetailView({ id }: { id: string }) {
  const { data, error, loading, reload } = useAsync(() => loadDetail(id), [id])
  const [generating, setGenerating] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [flash, setFlash] = useState<string | null>(null)

  async function generateProposal() {
    setGenerating(true)
    setActionError(null)
    try {
      await api.proposeFix(id)
      reload()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not generate a proposal.')
    } finally {
      setGenerating(false)
    }
  }

  if (error) return <ErrorState error={error} onRetry={reload} />
  if (!data) return loading ? <Loading label="Loading exception…" /> : null

  const { exception, timecard, proposal, history } = data
  const escalated = exception.status === 'ESCALATED' || proposal?.status === 'ESCALATED'

  return (
    <section>
      <Link to="/exceptions" className="back">
        ← All exceptions
      </Link>

      <div className="page-head">
        <div>
          <h1>{humanize(exception.type)}</h1>
          <p className="muted">
            <span title={exception.employee_id}>Employee {shortId(exception.employee_id)}</span> ·{' '}
            {formatDate(exception.work_date)}
          </p>
        </div>
        <div className="badges">
          <SeverityBadge severity={exception.severity} />
          <StatusBadge status={exception.status} />
        </div>
      </div>

      {escalated && (
        <div className="banner banner-escalated" role="alert">
          <strong>AI couldn't safely propose a fix, manager review needed.</strong>
          {proposal?.rationale && <div>{proposal.rationale}</div>}
        </div>
      )}
      {flash && (
        <div className="banner banner-success" role="status">
          {flash} <Link to={`/audit?entity_id=${proposal?.id ?? ''}`}>View in audit log</Link>
        </div>
      )}

      <div className="grid">
        <div className="card">
          <h2>What was detected</h2>
          <dl className="evidence">
            {Object.entries(exception.evidence).map(([key, value]) => (
              <div key={key}>
                <dt>{humanize(key)}</dt>
                <dd>{formatValue(value)}</dd>
              </div>
            ))}
          </dl>
          <div className="refs">
            Policy:{' '}
            {exception.policy_refs.map((ref) => (
              <span key={ref} className="clause-id" title={POLICY_CLAUSES[ref]?.text}>
                {ref} {POLICY_CLAUSES[ref]?.title}
              </span>
            ))}
          </div>
        </div>

        <div className="card">
          <h2>Timecard</h2>
          <Timeline timecard={timecard} proposedChanges={proposal?.fix.changes} />
          {timecard.notes && (
            <div className="notes">
              <div className="notes-label">
                Employee note <span className="muted small">(untrusted text, shown as written, never used as instructions)</span>
              </div>
              <p>{timecard.notes}</p>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Proposed fix</h2>
          {proposal && (
            <div className="badges">
              <SourceBadge source={proposal.source} />
              <StatusBadge status={proposal.status} />
            </div>
          )}
        </div>

        {!proposal && (
          <div>
            <p className="muted">No fix has been proposed yet.</p>
            <button type="button" className="btn btn-primary" onClick={generateProposal} disabled={generating}>
              {generating ? 'Generating…' : 'Generate proposal'}
            </button>
            {actionError && (
              <p className="form-error" role="alert">
                {actionError}
              </p>
            )}
          </div>
        )}

        {proposal && proposal.status !== 'ESCALATED' && (
          <>
            <FixDiff fix={proposal.fix} />
            <h3>Why</h3>
            <p className="rationale">{proposal.rationale}</p>
            <p className="muted small">
              Confidence {formatPercent(proposal.confidence)}
              {proposal.model && <> · model {proposal.model}</>} · prompt {proposal.prompt_version}
            </p>
            <h3>Policy cited</h3>
            <CitationList citations={proposal.citations} />
            <h3>Validator</h3>
            <ValidationPanel validation={proposal.validation} />

            {proposal.status === 'PENDING_REVIEW' ? (
              <>
                <h3>Your decision</h3>
                <DecisionForm
                  proposalId={proposal.id}
                  onDecided={(d) => {
                    setFlash(d.decision === 'APPROVE' ? 'Fix approved and applied.' : 'Fix rejected.')
                    reload()
                  }}
                />
              </>
            ) : (
              <p className="decided">
                This proposal was <strong>{humanize(proposal.status).toLowerCase()}</strong>.
              </p>
            )}
          </>
        )}

        {proposal && proposal.status === 'ESCALATED' && (
          <p className="muted">
            No automatic fix. Please resolve this case directly with the employee or the schedule.
          </p>
        )}
      </div>

      <div className="card">
        <h2>History</h2>
        {history.length === 0 ? (
          <p className="muted">No events yet.</p>
        ) : (
          <ol className="history">
            {history.map((ev) => (
              <li key={ev.id}>
                <span className="muted small">{formatDateTime(ev.ts)}</span>
                <span className="actor">{ev.actor}</span>
                <span>{humanize(ev.event_type)}</span>
              </li>
            ))}
          </ol>
        )}
        <Link to={`/audit?entity_id=${exception.id}`} className="small">
          Open full audit log
        </Link>
      </div>
    </section>
  )
}
