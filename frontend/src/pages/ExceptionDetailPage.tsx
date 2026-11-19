import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { Avatar } from '../components/Avatar'
import { SeverityBadge, SourceBadge, StatusBadge } from '../components/Badges'
import { Icon } from '../components/Icon'
import { CaseJourney } from '../components/CaseJourney'
import { CitationList } from '../components/CitationList'
import { DecisionForm } from '../components/DecisionForm'
import { FixDiff } from '../components/FixDiff'
import { ErrorState, Loading } from '../components/States'
import { Timeline } from '../components/Timeline'
import { ValidationPanel } from '../components/ValidationPanel'
import { useAsync } from '../hooks/useAsync'
import { formatDate, formatDateTime, formatPercent, humanize, shortId } from '../lib/format'
import { describeValue, orderEvidence } from '../lib/describe'
import { POLICY_CLAUSES } from '../lib/policy'
import { summarize } from '../lib/summary'
import type { AuditEvent, ExceptionDetail, Proposal } from '../types/api'

interface DetailWithHistory extends ExceptionDetail {
  history: AuditEvent[]
}

// Loads the case and the audit events for it, its timecard and its proposal.
async function loadDetail(id: string): Promise<DetailWithHistory> {
  const detail = await api.getExceptionDetail(id)
  const ids = [detail.exception.id, detail.timecard.id, detail.proposal?.id].filter(
    (x): x is string => Boolean(x),
  )
  const lists = await Promise.all(ids.map((entityId) => api.listAudit(entityId)))
  const seen = new Set<string>()
  const history = lists
    .flat()
    .filter((e) => !seen.has(e.id) && seen.add(e.id))
    .sort((a, b) => b.ts.localeCompare(a.ts))
  return { ...detail, history }
}

export function ExceptionDetailPage() {
  const { id = '' } = useParams()
  // key={id}: moving to another case starts with fresh page state
  return <ExceptionDetailView key={id} id={id} />
}

/** "AI could not safely propose a fix (no policy matched). Manager review needed."
 *  -> "No policy matched." The banner headline already says the first part. */
function escalationReason(rationale: string): string {
  const text = rationale
    .replace(/^AI could not safely propose a fix\s*[:(]?\s*/i, '')
    .replace(/\)?\.?\s*Manager review needed\.?\s*$/i, '')
    .trim()
  return text ? `${text.charAt(0).toUpperCase()}${text.slice(1)}.` : rationale
}

/** How sure the proposal is, as a bar. Rule-based fixes have a fixed value, so say so. */
function Confidence({ proposal }: { proposal: Proposal }) {
  const pct = Math.round(proposal.confidence * 100)
  const level = pct >= 80 ? 'high' : pct >= 50 ? 'medium' : 'low'
  return (
    <div className="confidence">
      <div className="confidence-label">
        <span>Confidence</span>
        <strong>{formatPercent(proposal.confidence)}</strong>
      </div>
      <div
        className={`meter meter-${level}`}
        role="meter"
        aria-label="Confidence"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <span style={{ width: `${pct}%` }} />
      </div>
      <p className="hint">
        {proposal.source === 'LLM'
          ? `Model ${proposal.model ?? 'unknown'}, prompt ${proposal.prompt_version}.`
          : 'Fixed value for rule-based fixes, not a model estimate.'}
      </p>
    </div>
  )
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
  if (!data) return loading ? <Loading label="Loading the case…" /> : null

  const { exception, timecard, proposal, decision, history } = data
  const escalated = exception.status === 'ESCALATED' || proposal?.status === 'ESCALATED'

  return (
    <section>
      <Link to="/exceptions" className="back">
        <Icon name="back" size={16} />
        Back to the queue
      </Link>

      <header className="case-head">
        <Avatar id={exception.employee_id} size="l" />
        <div className="case-head-text">
          <h1>{humanize(exception.type)}</h1>
          <p className="case-meta">
            {summarize(exception)}.{' '}
            <span title={exception.employee_id}>Employee {shortId(exception.employee_id)}</span> on{' '}
            {formatDate(exception.work_date)}.
          </p>
        </div>
        <div className="badges">
          <SeverityBadge severity={exception.severity} />
          <StatusBadge status={exception.status} />
        </div>
      </header>

      <CaseJourney exception={exception} proposal={proposal} decision={decision} />

      {escalated && (
        <div className="banner banner-escalated" role="alert">
          <strong>AI couldn't safely propose a fix, manager review needed.</strong>
          {proposal?.rationale && <div>{escalationReason(proposal.rationale)}</div>}
        </div>
      )}
      {flash && (
        <div className="banner banner-success" role="status">
          {flash} <Link to={`/audit?entity_id=${proposal?.id ?? ''}`}>See it in the audit log</Link>
        </div>
      )}

      <div className="case-layout">
        <div className="case-main">
          <section className="panel">
            <h2>What the rule check found</h2>
            <dl className="evidence">
              {orderEvidence(exception.evidence).map(([key, value]) => (
                <div key={key}>
                  <dt>{humanize(key)}</dt>
                  <dd>{describeValue(value, timecard)}</dd>
                </div>
              ))}
            </dl>
            <div className="refs">
              {exception.policy_refs.map((ref) => (
                <span key={ref} className="clause-id" title={POLICY_CLAUSES[ref]?.text}>
                  {ref} {POLICY_CLAUSES[ref]?.title}
                </span>
              ))}
            </div>
          </section>

          <section className="panel">
            <h2>Timecard</h2>
            <Timeline timecard={timecard} proposedChanges={proposal?.fix.changes} />
            {timecard.notes && (
              <div className="notes">
                <div className="notes-label">
                  Employee note{' '}
                  <span className="hint">
                    (untrusted text, shown as written, never used as instructions)
                  </span>
                </div>
                <p>{timecard.notes}</p>
              </div>
            )}
          </section>

          <section className="panel">
            <h2>History</h2>
            {history.length === 0 ? (
              <p className="hint">No events yet.</p>
            ) : (
              <ol className="history">
                {history.map((ev) => (
                  <li key={ev.id}>
                    <time className="history-time">{formatDateTime(ev.ts)}</time>
                    <span
                      className={`actor actor-${ev.actor === 'system' || ev.actor === 'llm' ? ev.actor : 'manager'}`}
                    >
                      {ev.actor}
                    </span>
                    <span>{humanize(ev.event_type)}</span>
                  </li>
                ))}
              </ol>
            )}
            <Link to={`/audit?entity_id=${exception.id}`} className="more-link">
              Open the full audit log
            </Link>
          </section>
        </div>

        <aside className="decision-panel" aria-label="Proposed fix and decision">
          <div className="decision-panel-head">
            <h2>Proposed fix</h2>
            {proposal &&
              (proposal.status === 'ESCALATED' ? (
                <span className="badge status-escalated">No fix</span>
              ) : (
                <SourceBadge source={proposal.source} />
              ))}
          </div>

          {!proposal && (
            <div className="no-proposal">
              <p>
                No fix has been proposed yet. The AI will look up the policy, suggest a fix, and the
                validator will check it before you see it.
              </p>
              <button
                type="button"
                className="btn btn-primary"
                onClick={generateProposal}
                disabled={generating}
              >
                {generating ? 'Generating…' : 'Generate proposal'}
              </button>
              {actionError && (
                <p className="form-error" role="alert">
                  {actionError}
                </p>
              )}
            </div>
          )}

          {proposal && proposal.status === 'ESCALATED' && (
            <div className="escalated-help">
              <p>
                There is no automatic fix to approve. Resolve this case directly with the employee
                or in the schedule, using the policy below.
              </p>
              {exception.policy_refs.map((ref) =>
                POLICY_CLAUSES[ref] ? (
                  <div key={ref} className="citation">
                    <div className="citation-head">
                      <span className="clause-id">{ref}</span>
                      <span>{POLICY_CLAUSES[ref].title}</span>
                    </div>
                    <p className="clause-text">{POLICY_CLAUSES[ref].text}</p>
                  </div>
                ) : null,
              )}
              <p className="hint">
                Whatever you decide is recorded in the audit log with your name.
              </p>
            </div>
          )}

          {proposal && proposal.status !== 'ESCALATED' && (
            <>
              <FixDiff fix={proposal.fix} timecard={timecard} />
              <h3>Why</h3>
              <p className="rationale">{proposal.rationale}</p>
              <Confidence proposal={proposal} />
              <h3>Policy cited</h3>
              <CitationList citations={proposal.citations} />
              <h3>Validator</h3>
              <ValidationPanel validation={proposal.validation} />

              {proposal.status === 'PENDING_REVIEW' ? (
                <>
                  <h3>Your decision</h3>
                  <DecisionForm
                    caseId={exception.id}
                    onDecided={(d) => {
                      setFlash(d === 'APPROVE' ? 'Fix approved and applied.' : 'Fix rejected.')
                      reload()
                    }}
                  />
                </>
              ) : (
                <div className={`decided decided-${proposal.status.toLowerCase()}`}>
                  <p>
                    This proposal was <strong>{humanize(proposal.status).toLowerCase()}</strong>
                    {decision && (
                      <>
                        {' '}
                        by <strong>{decision.manager_id}</strong> on{' '}
                        {formatDateTime(decision.decided_at)}
                      </>
                    )}
                    .
                  </p>
                  {decision?.comment && (
                    <blockquote className="decision-comment">“{decision.comment}”</blockquote>
                  )}
                  {proposal.status === 'REJECTED' && (
                    <>
                      <p className="hint">Nothing was changed on the timecard.</p>
                      <button
                        type="button"
                        className="btn"
                        onClick={generateProposal}
                        disabled={generating}
                      >
                        {generating ? 'Generating…' : 'Ask for a new proposal'}
                      </button>
                    </>
                  )}
                </div>
              )}
            </>
          )}
        </aside>
      </div>
    </section>
  )
}
