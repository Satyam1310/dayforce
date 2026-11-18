// Result of the deterministic validator that checks every proposal before a manager sees it.
import type { ValidationResult } from '../types/api'

export function ValidationPanel({ validation }: { validation: ValidationResult | null }) {
  if (!validation) {
    return <div className="validation validation-none">Not validated (no fix to check).</div>
  }
  if (validation.passed) {
    return (
      <div className="validation validation-pass">
        ✓ Passed all policy checks. Applying this fix creates no new violations.
      </div>
    )
  }
  return (
    <div className="validation validation-fail" role="alert">
      <strong>✗ Failed validation ({validation.violations.length})</strong>
      <ul>
        {validation.violations.map((v, i) => (
          <li key={`${v.code}-${i}`}>
            <code>{v.code}</code> {v.message}
            {v.clause_id && <span className="clause-id small">{v.clause_id}</span>}
          </li>
        ))}
      </ul>
    </div>
  )
}
