// The proposed fix as a before / after table.
import { describeValue, fieldLabel } from '../lib/describe'
import { humanize } from '../lib/format'
import type { Fix, Timecard } from '../types/api'

export function FixDiff({ fix, timecard }: { fix: Fix; timecard?: Timecard }) {
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
                <td title={c.field}>{fieldLabel(c.field)}</td>
                <td className="diff-old">{describeValue(c.old, timecard)}</td>
                <td className="diff-new">{describeValue(c.new, timecard)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
