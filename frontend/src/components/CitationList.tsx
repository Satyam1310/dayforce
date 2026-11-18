// The policy clauses the AI cited, with the quote it used and the full clause text.
import { formatPercent } from '../lib/format'
import { POLICY_CLAUSES } from '../lib/policy'
import type { PolicyCitation } from '../types/api'

export function CitationList({ citations }: { citations: PolicyCitation[] }) {
  if (citations.length === 0) {
    return <p className="muted">No policy clause cited.</p>
  }
  return (
    <ul className="citations">
      {citations.map((c) => {
        const clause = POLICY_CLAUSES[c.clause_id]
        return (
          <li key={c.clause_id} className="citation">
            <div className="citation-head">
              <span className="clause-id">{c.clause_id}</span>
              {clause && <span>{clause.title}</span>}
              <span className="muted small">match {formatPercent(c.score)}</span>
            </div>
            <blockquote>“{c.quote}”</blockquote>
            {clause && (
              <details>
                <summary>Full clause</summary>
                <p>{clause.text}</p>
              </details>
            )}
          </li>
        )
      })}
    </ul>
  )
}
