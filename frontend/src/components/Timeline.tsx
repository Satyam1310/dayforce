// Visual timeline of one timecard: scheduled shifts vs actual punches,
// plus any punch the proposal wants to add (shown dashed).
import { formatTime, isIsoDateTime } from '../lib/format'
import type { FieldChange, PunchType, Timecard } from '../types/api'

const HOUR = 60 * 60 * 1000

const PUNCH_LABEL: Record<PunchType, string> = {
  IN: 'In',
  OUT: 'Out',
  BREAK_START: 'Break',
  BREAK_END: 'Back',
}

interface Props {
  timecard: Timecard
  proposedChanges?: FieldChange[]
}

export function Timeline({ timecard, proposedChanges = [] }: Props) {
  // Proposed punches: changes like { field: "punch.OUT", new: "<iso time>" }
  const proposed = proposedChanges
    .filter((c) => c.field.startsWith('punch.') && isIsoDateTime(c.new))
    .map((c) => ({ label: `${humanPunch(c.field)} (proposed)`, ts: Date.parse(c.new as string) }))

  const times = [
    ...timecard.shifts.flatMap((s) => [Date.parse(s.start), Date.parse(s.end)]),
    ...timecard.punches.map((p) => Date.parse(p.timestamp)),
    ...proposed.map((p) => p.ts),
  ]
  if (times.length === 0) {
    return <p className="muted">No shifts or punches on this timecard.</p>
  }

  // Window: one hour of padding either side, snapped to whole hours.
  const min = Math.floor(Math.min(...times) / HOUR) * HOUR - HOUR
  const max = Math.ceil(Math.max(...times) / HOUR) * HOUR + HOUR
  const pct = (t: number) => `${((t - min) / (max - min)) * 100}%`
  const step = (max - min) / HOUR > 14 ? 2 * HOUR : HOUR
  const ticks: number[] = []
  for (let t = min; t <= max; t += step) ticks.push(t)

  const breaks = pairBreaks(timecard)

  return (
    <div className="timeline" aria-label="Timecard timeline">
      <div className="tl-row">
        <div className="tl-label" />
        <div className="tl-track tl-axis">
          {ticks.map((t) => (
            <span key={t} className="tl-tick" style={{ left: pct(t) }}>
              {formatTime(new Date(t).toISOString())}
            </span>
          ))}
        </div>
      </div>

      {timecard.shifts.length === 0 && (
        <div className="tl-row">
          <div className="tl-label">Scheduled</div>
          <div className="tl-track">
            <span className="tl-empty">No shift scheduled</span>
          </div>
        </div>
      )}
      {timecard.shifts.map((s, i) => {
        const start = Date.parse(s.start)
        const end = Date.parse(s.end)
        return (
          <div className="tl-row" key={s.id}>
            <div className="tl-label">
              {timecard.shifts.length > 1 ? `Shift ${i + 1}` : 'Scheduled'}
            </div>
            <div className="tl-track">
              <div
                className="tl-bar"
                style={{ left: pct(start), width: `calc(${pct(end)} - ${pct(start)})` }}
                title={`${formatTime(s.start)}–${formatTime(s.end)} at ${s.location_id}`}
              >
                {formatTime(s.start)}–{formatTime(s.end)} · {s.location_id}
              </div>
            </div>
          </div>
        )
      })}

      <div className="tl-row">
        <div className="tl-label">Punches</div>
        <div className="tl-track">
          {breaks.map((b) => (
            <div
              key={b.start}
              className="tl-break"
              style={{ left: pct(b.start), width: `calc(${pct(b.end)} - ${pct(b.start)})` }}
              title="Break"
            />
          ))}
          {timecard.punches.map((p) => (
            <span
              key={p.id}
              className={`tl-marker punch-${p.type.toLowerCase()}`}
              style={{ left: pct(Date.parse(p.timestamp)) }}
              title={`${PUNCH_LABEL[p.type]} at ${formatTime(p.timestamp)}`}
            >
              {PUNCH_LABEL[p.type]} {formatTime(p.timestamp)}
            </span>
          ))}
          {proposed.map((p) => (
            <span
              key={`proposed-${p.ts}`}
              className="tl-marker tl-proposed"
              style={{ left: pct(p.ts) }}
              title={`${p.label} at ${formatTime(new Date(p.ts).toISOString())}`}
            >
              {p.label} {formatTime(new Date(p.ts).toISOString())}
            </span>
          ))}
        </div>
      </div>
      <p className="muted small">Times in IST (Asia/Kolkata).</p>
    </div>
  )
}

function humanPunch(field: string): string {
  const type = field.split('.')[1] as PunchType | undefined
  return type && type in PUNCH_LABEL ? PUNCH_LABEL[type] : field
}

function pairBreaks(timecard: Timecard): { start: number; end: number }[] {
  const sorted = [...timecard.punches].sort((a, b) => a.timestamp.localeCompare(b.timestamp))
  const out: { start: number; end: number }[] = []
  let open: number | null = null
  for (const p of sorted) {
    if (p.type === 'BREAK_START') open = Date.parse(p.timestamp)
    if (p.type === 'BREAK_END' && open !== null) {
      out.push({ start: open, end: Date.parse(p.timestamp) })
      open = null
    }
  }
  return out
}
