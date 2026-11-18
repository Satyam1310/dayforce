// The proposed fix as a before / after table.
import { formatValue, humanize } from '../lib/format'
import type { Fix } from '../types/api'

export function FixDiff({ fix }: { fix: Fix }) {
  return (
    <div className="fix">
      <div className="fix-action">
        Action: <strong>{humanize(fix.action)}</strong>
      </div>
      {fix.changes.length === 0 ? (
        <p className="muted">No changes to the timecard.</p>
      ) : (
        <table className="table diff">
          <thead>
            <tr>
              <th>Field</th>
              <th>Before</th>
              <th>After</th>
            </tr>
          </thead>
          <tbody>
            {fix.changes.map((c, i) => (
              <tr key={`${c.target_id}-${c.field}-${i}`}>
                <td>
                  <code>{c.field}</code>
                </td>
                <td className="diff-old">{formatValue(c.old)}</td>
                <td className="diff-new">{formatValue(c.new)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
