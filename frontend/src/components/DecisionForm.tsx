// Manager Approve / Reject. This is the only place a timecard change can be
// triggered: the AI never changes pay on its own (POL-TA-07).
import { useState, type FormEvent } from 'react'
import { api } from '../api'
import { CURRENT_MANAGER_ID } from '../config'
import type { DecisionValue } from '../types/api'

interface Props {
  caseId: string
  onDecided: (decision: DecisionValue) => void
}

export function DecisionForm({ caseId, onDecided }: Props) {
  const [comment, setComment] = useState('')
  const [submitting, setSubmitting] = useState<DecisionValue | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function submit(decision: DecisionValue) {
    const trimmed = comment.trim()
    // A rejection must say why, so the audit trail records the reason.
    if (decision === 'REJECT' && !trimmed) {
      setError('Please add a comment explaining why you are rejecting this fix.')
      return
    }
    setError(null)
    setSubmitting(decision)
    try {
      await api.decide(caseId, {
        decision,
        manager_id: CURRENT_MANAGER_ID,
        comment: trimmed || null,
      })
      onDecided(decision)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the decision.')
    } finally {
      setSubmitting(null)
    }
  }

  return (
    <form className="decision" onSubmit={(e: FormEvent) => e.preventDefault()}>
      <label htmlFor="decision-comment">Comment (required to reject)</label>
      <textarea
        id="decision-comment"
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        placeholder="e.g. Confirmed with the employee, they left at 18:00."
        rows={3}
        maxLength={500}
      />
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="decision-buttons">
        <button
          type="button"
          className="btn btn-approve"
          disabled={submitting !== null}
          onClick={() => submit('APPROVE')}
        >
          {submitting === 'APPROVE' ? 'Approving…' : 'Approve fix'}
        </button>
        <button
          type="button"
          className="btn btn-reject"
          disabled={submitting !== null}
          onClick={() => submit('REJECT')}
        >
          {submitting === 'REJECT' ? 'Rejecting…' : 'Reject'}
        </button>
      </div>
      <p className="muted small">
        Approving applies the change to the timecard. Your decision is logged.
      </p>
    </form>
  )
}
